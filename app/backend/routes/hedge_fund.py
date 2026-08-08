from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import asyncio
import logging
import uuid

from app.backend.database import get_db
from app.backend.models.schemas import ErrorResponse, HedgeFundRequest, BacktestRequest, BacktestDayResult, BacktestPerformanceMetrics
from app.backend.models.events import StartEvent, ProgressUpdateEvent, ErrorEvent, CompleteEvent
from app.backend.services.graph import create_graph, parse_hedge_fund_response, run_graph_async
from app.backend.services.portfolio import create_portfolio
from app.backend.services.backtest_service import BacktestService
from app.backend.services.api_key_service import ApiKeyService
from src.utils.progress import progress
from src.utils.analysts import get_agents_list

router = APIRouter(prefix="/hedge-fund")

logger = logging.getLogger(__name__)

@router.post(
    path="/run",
    responses={
        200: {"description": "Successful response with streaming updates"},
        400: {"model": ErrorResponse, "description": "Invalid request parameters"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def run(request_data: HedgeFundRequest, request: Request, db: Session = Depends(get_db)):
    try:
        # Hydrate API keys from database if not provided
        if not request_data.api_keys:
            api_key_service = ApiKeyService(db)
            request_data.api_keys = api_key_service.get_api_keys_dict()

        # ── Queue mode (Phase 2f): Redis available → execute in the arq worker.
        # API keys are already hydrated into the payload above (the worker has
        # no request-scoped DB session). Progress streams back over
        # progress_bus; the parsed final payload is fetched from arq's result
        # store after the terminal event. Falls through to the in-process path
        # when Redis is unavailable or enqueueing fails.
        from app.backend.services import queue_client
        if await queue_client.queue_mode_enabled():
            try:
                _queued = await _start_queue_run(request_data)
            except Exception as exc:
                logger.warning("queue mode unavailable (%s) — in-process fallback", exc)
                _queued = None
            if _queued is not None:
                return _queued

        # Create the portfolio
        portfolio = create_portfolio(request_data.initial_cash, request_data.margin_requirement, request_data.tickers, request_data.portfolio_positions)

        # Construct agent graph using the React Flow graph structure
        graph = create_graph(
            graph_nodes=request_data.graph_nodes,
            graph_edges=request_data.graph_edges
        )
        graph = graph.compile()

        # Log a test progress update for debugging
        progress.update_status("system", None, "Preparing hedge fund run")

        # Convert model_provider to string if it's an enum
        model_provider = request_data.model_provider
        if hasattr(model_provider, "value"):
            model_provider = model_provider.value

        # Function to detect client disconnection
        async def wait_for_disconnect():
            """Wait for client disconnect and return True when it happens"""
            try:
                while True:
                    message = await request.receive()
                    if message["type"] == "http.disconnect":
                        return True
            except Exception:
                return True

        # Set up streaming response
        async def event_generator():
            # Queue for progress updates
            progress_queue = asyncio.Queue()
            run_task = None
            disconnect_task = None

            # Generate a unique run_id for this execution
            run_id = uuid.uuid4().hex[:16]

            # Handler with run_id filtering to prevent cross-run event leakage
            def progress_handler(agent_name, ticker, status, analysis, timestamp, partial_data=None, event_run_id=None):
                # Filter: only accept events from this run (event_run_id from ContextVar)
                if event_run_id is not None and event_run_id != run_id:
                    return
                event = ProgressUpdateEvent(agent=agent_name, ticker=ticker, status=status, timestamp=timestamp, analysis=analysis)
                progress_queue.put_nowait(event)

            # Register our handler with the progress tracker
            progress.register_handler(progress_handler)

            try:
                # Start the graph execution in a background task
                run_task = asyncio.create_task(
                    run_graph_async(
                        graph=graph,
                        portfolio=portfolio,
                        tickers=request_data.tickers,
                        start_date=request_data.start_date,
                        end_date=request_data.end_date,
                        model_name=request_data.model_name,
                        model_provider=model_provider,
                        request=request_data,  # Pass the full request for agent-specific model access
                    )
                )
                
                # Start the disconnect detection task
                disconnect_task = asyncio.create_task(wait_for_disconnect())
                
                # Send initial message
                yield StartEvent().to_sse()

                # Stream progress updates until run_task completes or client disconnects
                while not run_task.done():
                    # Check if client disconnected
                    if disconnect_task.done():
                        print("Client disconnected, cancelling hedge fund execution")
                        run_task.cancel()
                        try:
                            await run_task
                        except asyncio.CancelledError:
                            pass
                        return

                    # Either get a progress update or wait a bit
                    try:
                        event = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                        yield event.to_sse()
                    except asyncio.TimeoutError:
                        # Just continue the loop
                        pass

                # Get the final result
                try:
                    result = await run_task
                except asyncio.CancelledError:
                    print("Task was cancelled")
                    return

                if not result or not result.get("messages"):
                    yield ErrorEvent(message="Failed to generate hedge fund decisions").to_sse()
                    return

                # Send the final result
                final_data = CompleteEvent(
                    data={
                        "decisions": parse_hedge_fund_response(result.get("messages", [])[-1].content),
                        "analyst_signals": result.get("data", {}).get("analyst_signals", {}),
                        "current_prices": result.get("data", {}).get("current_prices", {}),
                    }
                )
                yield final_data.to_sse()

            except asyncio.CancelledError:
                print("Event generator cancelled")
                return
            finally:
                # Clean up
                progress.unregister_handler(progress_handler)
                if run_task and not run_task.done():
                    run_task.cancel()
                    try:
                        await run_task
                    except asyncio.CancelledError:
                        pass
                if disconnect_task and not disconnect_task.done():
                    disconnect_task.cancel()

        # Return a streaming response
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred while processing the request: {str(e)}")


# ── Queue-mode execution (Phase 2f) ──────────────────────────────────────────
# With Redis available the agent graph runs in the arq worker. The SSE stream
# is sourced from progress_bus (Redis pub/sub + replay buffer); the parsed
# final payload comes from arq's result store after graph_complete. Client
# disconnect no longer cancels the run (the worker finishes and the result
# stays retrievable for keep_result seconds) — same trade-off as /analysis/run.

_QUEUE_STREAM_DEADLINE = 1800  # matches worker job_timeout


async def _start_queue_run(request_data: HedgeFundRequest):
    """Enqueue the graph run and return a bus-backed StreamingResponse.

    Raises on enqueue failure — the route catches it and falls back to the
    in-process path.
    """
    from app.backend.services import queue_client

    run_id = uuid.uuid4().hex[:16]  # same format as the in-process path
    payload = request_data.model_dump(mode="json")
    job = await queue_client.enqueue_hedge_fund_run(run_id, None, payload)
    return StreamingResponse(
        _queue_stream_generator(run_id, job),
        media_type="text/event-stream",
    )


async def _queue_stream_generator(run_id: str, job):
    """SSE stream sourced from progress_bus + arq result store. Emits the same
    event contract as the in-process path: start / progress_update / complete / error."""
    from app.backend.services import progress_bus

    yield StartEvent().to_sse()

    loop = asyncio.get_event_loop()
    deadline = loop.time() + _QUEUE_STREAM_DEADLINE
    stream = progress_bus.iter_events(run_id)
    while True:
        try:
            event = await asyncio.wait_for(stream.__anext__(), timeout=60.0)
        except asyncio.TimeoutError:
            if loop.time() > deadline:
                yield ErrorEvent(
                    message="Timed out waiting for the hedge fund worker (30 min)."
                ).to_sse()
                return
            continue
        except StopAsyncIteration:
            yield ErrorEvent(message="Progress stream ended unexpectedly.").to_sse()
            return

        if event.get("completed"):
            if event.get("phase") == "graph_error" or event.get("status") == "error":
                yield ErrorEvent(
                    message=event.get("summary", "Hedge fund run failed")
                ).to_sse()
                return
            # graph_complete — fetch the parsed final payload from the result store
            try:
                result = await job.result(timeout=60)
            except Exception as exc:
                yield ErrorEvent(
                    message=f"Failed to retrieve run result: {exc}"
                ).to_sse()
                return
            final_data = result.get("final_data") if isinstance(result, dict) else None
            if not final_data:
                # Same error the in-process path emits for an empty result
                yield ErrorEvent(
                    message="Failed to generate hedge fund decisions"
                ).to_sse()
                return
            yield CompleteEvent(data=final_data).to_sse()
            return

        if event.get("phase") == "agent_progress":
            yield ProgressUpdateEvent(
                agent=event.get("agent"),
                ticker=event.get("ticker"),
                status=event.get("status"),
                timestamp=event.get("timestamp"),
                analysis=event.get("analysis"),
            ).to_sse()


@router.post(
    path="/backtest",
    responses={
        200: {"description": "Successful response with streaming backtest updates"},
        400: {"model": ErrorResponse, "description": "Invalid request parameters"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def backtest(request_data: BacktestRequest, request: Request, db: Session = Depends(get_db)):
    """Run a continuous backtest over a time period with streaming updates."""
    try:
        # Hydrate API keys from database if not provided
        if not request_data.api_keys:
            api_key_service = ApiKeyService(db)
            request_data.api_keys = api_key_service.get_api_keys_dict()

        # Convert model_provider to string if it's an enum
        model_provider = request_data.model_provider
        if hasattr(model_provider, "value"):
            model_provider = model_provider.value

        # Create the portfolio (same as /run endpoint)
        portfolio = create_portfolio(
            request_data.initial_capital, 
            request_data.margin_requirement, 
            request_data.tickers, 
            request_data.portfolio_positions
        )

        # Construct agent graph using the React Flow graph structure (same as /run endpoint)
        graph = create_graph(graph_nodes=request_data.graph_nodes, graph_edges=request_data.graph_edges)
        graph = graph.compile()

        # Create backtest service with the compiled graph
        backtest_service = BacktestService(
            graph=graph,
            portfolio=portfolio,
            tickers=request_data.tickers,
            start_date=request_data.start_date,
            end_date=request_data.end_date,
            initial_capital=request_data.initial_capital,
            model_name=request_data.model_name,
            model_provider=model_provider,
            request=request_data,  # Pass the full request for agent-specific model access
        )

        # Function to detect client disconnection
        async def wait_for_disconnect():
            """Wait for client disconnect and return True when it happens"""
            try:
                while True:
                    message = await request.receive()
                    if message["type"] == "http.disconnect":
                        return True
            except Exception:
                return True

        # Set up streaming response
        async def event_generator():
            progress_queue = asyncio.Queue()
            backtest_task = None
            disconnect_task = None

            # Generate a unique run_id for this backtest execution
            backtest_run_id = uuid.uuid4().hex[:16]

            # Progress handler with run_id filtering to prevent cross-run event leakage
            def progress_handler(agent_name, ticker, status, analysis, timestamp, partial_data=None, event_run_id=None):
                # Filter: only accept events from this run (event_run_id from ContextVar)
                if event_run_id is not None and event_run_id != backtest_run_id:
                    return
                event = ProgressUpdateEvent(agent=agent_name, ticker=ticker, status=status, timestamp=timestamp, analysis=analysis)
                progress_queue.put_nowait(event)

            # Progress callback to handle backtest-specific updates
            def progress_callback(update):
                if update["type"] == "progress":
                    event = ProgressUpdateEvent(
                        agent="backtest",
                        ticker=None,
                        status=f"Processing {update['current_date']} ({update['current_step']}/{update['total_dates']})",
                        timestamp=None,
                        analysis=None
                    )
                    progress_queue.put_nowait(event)
                elif update["type"] == "backtest_result":
                    # Convert day result to a streaming event
                    backtest_result = BacktestDayResult(**update["data"])
                    
                    # Send the full day result data as JSON in the analysis field
                    import json
                    analysis_data = json.dumps(update["data"])
                    
                    event = ProgressUpdateEvent(
                        agent="backtest",
                        ticker=None,
                        status=f"Completed {backtest_result.date} - Portfolio: ${backtest_result.portfolio_value:,.2f}",
                        timestamp=None,
                        analysis=analysis_data
                    )
                    progress_queue.put_nowait(event)

            # Register our handler with the progress tracker to capture agent updates
            progress.register_handler(progress_handler)
            
            try:
                # Start the backtest in a background task
                backtest_task = asyncio.create_task(
                    backtest_service.run_backtest_async(progress_callback=progress_callback)
                )
                
                # Start the disconnect detection task
                disconnect_task = asyncio.create_task(wait_for_disconnect())
                
                # Send initial message
                yield StartEvent().to_sse()

                # Stream progress updates until backtest_task completes or client disconnects
                while not backtest_task.done():
                    # Check if client disconnected
                    if disconnect_task.done():
                        print("Client disconnected, cancelling backtest execution")
                        backtest_task.cancel()
                        try:
                            await backtest_task
                        except asyncio.CancelledError:
                            pass
                        return

                    # Either get a progress update or wait a bit
                    try:
                        event = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                        yield event.to_sse()
                    except asyncio.TimeoutError:
                        # Just continue the loop
                        pass

                # Get the final result
                try:
                    result = await backtest_task
                except asyncio.CancelledError:
                    print("Backtest task was cancelled")
                    return

                if not result:
                    yield ErrorEvent(message="Failed to complete backtest").to_sse()
                    return

                # Send the final result
                performance_metrics = BacktestPerformanceMetrics(**result["performance_metrics"])
                final_data = CompleteEvent(
                    data={
                        "performance_metrics": performance_metrics.model_dump(),
                        "final_portfolio": result["final_portfolio"],
                        "total_days": len(result["results"]),
                    }
                )
                yield final_data.to_sse()

            except asyncio.CancelledError:
                print("Backtest event generator cancelled")
                return
            finally:
                # Clean up
                progress.unregister_handler(progress_handler)
                if backtest_task and not backtest_task.done():
                    backtest_task.cancel()
                    try:
                        await backtest_task
                    except asyncio.CancelledError:
                        pass
                if disconnect_task and not disconnect_task.done():
                    disconnect_task.cancel()

        # Return a streaming response
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred while processing the backtest request: {str(e)}")


@router.get(
    path="/agents",
    responses={
        200: {"description": "List of available agents"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_agents():
    """Get the list of available agents."""
    try:
        return {"agents": get_agents_list()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve agents: {str(e)}")

