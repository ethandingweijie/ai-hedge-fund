import React from 'react';
import ReactDOM from 'react-dom/client';

import App from './App';
import { NodeProvider } from './contexts/node-context';
import { installAuthFetch } from './lib/auth-fetch';

import './index.css';

// Must run before any component issues a request: the backend denies
// unauthenticated calls on all non-public routes.
installAuthFetch();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <NodeProvider>
      <App />
    </NodeProvider>
  </React.StrictMode>
);
