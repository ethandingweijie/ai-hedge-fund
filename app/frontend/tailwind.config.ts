import tailwindcssTypography from '@tailwindcss/typography';
import type { Config } from 'tailwindcss';
import tailwindcssAnimate from 'tailwindcss-animate';

const config: Config = {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
  	fontFamily: {
  		sans: ['var(--font-sans)'],
  		// `mono` intentionally resolves to the same geometric sans. The 184
  		// existing font-mono call sites are tickers/prices/counters, not code,
  		// and the previous config already pointed mono at the body face. The
  		// numeric utilities supply tnum, which is what those sites wanted.
  		mono: ['var(--font-numeric)']
  	},
  	extend: {
  		// ── Uber Base type scale · 1.125 Major Second, 4px baseline ─────────
  		// Every line-height is a multiple of 4. Weights are the spec defaults;
  		// override with font-medium/semibold where the spec lists a pair.
  		fontSize: {
  			'display-lg': ['96px', { lineHeight: '112px', fontWeight: '700', letterSpacing: '-0.035em' }],
  			'display-md': ['52px', { lineHeight: '64px', fontWeight: '700', letterSpacing: '-0.03em' }],
  			'heading-xl': ['36px', { lineHeight: '44px', fontWeight: '700', letterSpacing: '-0.025em' }],
  			'heading-lg': ['32px', { lineHeight: '40px', fontWeight: '700', letterSpacing: '-0.022em' }],
  			'heading-md': ['28px', { lineHeight: '36px', fontWeight: '600', letterSpacing: '-0.02em' }],
  			'heading-sm': ['24px', { lineHeight: '32px', fontWeight: '600', letterSpacing: '-0.018em' }],
  			'heading-xs': ['20px', { lineHeight: '28px', fontWeight: '600', letterSpacing: '-0.015em' }],
  			'paragraph-lg': ['18px', { lineHeight: '28px', fontWeight: '400' }],
  			'paragraph-md': ['16px', { lineHeight: '24px', fontWeight: '400' }],
  			'paragraph-sm': ['14px', { lineHeight: '20px', fontWeight: '400' }],
  			'paragraph-xs': ['12px', { lineHeight: '16px', fontWeight: '400' }],
  			// Retained legacy aliases so existing call sites keep compiling.
  			title: ['0.875rem', { lineHeight: '1.25rem' }],
  			subtitle: ['0.625rem', { lineHeight: '1rem' }]
  		},
  		// ── Corner radii · strict hierarchy ─────────────────────────────────
  		// tag 4 → control 8 → card 16 → sheet 24. The shadcn aliases below are
  		// remapped so existing rounded-md buttons and rounded-lg cards land on
  		// the right tier without touching every call site.
  		borderRadius: {
  			none: '0px',
  			sm: 'var(--radius-tag)',       /* 4px  compact tags */
  			md: 'var(--radius-control)',   /* 8px  inputs, buttons */
  			lg: 'var(--radius-card)',      /* 16px cards */
  			xl: 'var(--radius-card)',      /* 16px cards */
  			'2xl': 'var(--radius-sheet)',  /* 24px sheets */
  			tag: 'var(--radius-tag)',
  			control: 'var(--radius-control)',
  			card: 'var(--radius-card)',
  			sheet: 'var(--radius-sheet)'
  		},
  		// ── Elevation · layered surface shadows, not border outlines ────────
  		boxShadow: {
  			'elevation-1': '0 -8px 24px rgb(0 0 0 / 0.6)',
  			'elevation-2': '0 2px 8px rgb(0 0 0 / 0.3)',
  			'elevation-3': '0 12px 32px rgb(0 0 0 / 0.8)'
  		},
  		colors: {
  			background: 'hsl(var(--background) / <alpha-value>)',
  			foreground: 'hsl(var(--foreground) / <alpha-value>)',
  			card: {
  				DEFAULT: 'hsl(var(--card))',
  				foreground: 'hsl(var(--card-foreground))'
  			},
  			popover: {
  				DEFAULT: 'hsl(var(--popover))',
  				foreground: 'hsl(var(--popover-foreground))'
  			},
  			primary: {
  				DEFAULT: 'hsl(var(--primary))',
  				foreground: 'hsl(var(--primary-foreground))'
  			},
  			brand: 'hsl(var(--brand) / <alpha-value>)',
  			hero: {
  				DEFAULT: 'hsl(var(--hero) / <alpha-value>)',
  				foreground: 'hsl(var(--hero-foreground) / <alpha-value>)'
  			},
  			secondary: {
  				DEFAULT: 'hsl(var(--secondary))',
  				foreground: 'hsl(var(--secondary-foreground))'
  			},
  			muted: {
  				DEFAULT: 'hsl(var(--muted) / <alpha-value>)',
  				foreground: 'hsl(var(--muted-foreground) / <alpha-value>)'
  			},
  			accent: {
  				DEFAULT: 'hsl(var(--accent))',
  				foreground: 'hsl(var(--accent-foreground))'
  			},
  			destructive: {
  				DEFAULT: 'hsl(var(--destructive))',
  				foreground: 'hsl(var(--destructive-foreground))'
  			},
  			panel: 'hsl(var(--panel-bg))',
  			// ── Surface elevation tiers ─────────────────────────────────────
  			surface: {
  				'0': 'hsl(var(--surface-0) / <alpha-value>)',
  				'1': 'hsl(var(--surface-1) / <alpha-value>)',
  				'2': 'hsl(var(--surface-2) / <alpha-value>)',
  				'3': 'hsl(var(--surface-3) / <alpha-value>)',
  				'2-hover': 'hsl(var(--surface-2-hover) / <alpha-value>)',
  				'2-active': 'hsl(var(--surface-2-active) / <alpha-value>)'
  			},
  			// ── Text emphasis ramp ──────────────────────────────────────────
  			content: {
  				high: 'hsl(var(--text-high) / <alpha-value>)',
  				medium: 'hsl(var(--text-medium) / <alpha-value>)',
  				muted: 'hsl(var(--text-muted) / <alpha-value>)',
  				disabled: 'hsl(var(--text-disabled) / <alpha-value>)'
  			},
  			// ── Price-change direction — RESERVED ───────────────────────────
  			// The only chromatic green/red in the system. Use text-gain /
  			// text-loss for price deltas, % upside, returns and P&L ONLY.
  			// Status, health, pass/fail and progress are monochrome — reach
  			// for the surface tiers and content ramp instead.
  			gain: 'hsl(var(--gain) / <alpha-value>)',
  			loss: 'hsl(var(--loss) / <alpha-value>)',
  			warning: 'hsl(var(--warning) / <alpha-value>)',
  			'ramp-grey': {
  				'100': 'var(--ramp-grey-100)',
  				'200': 'var(--ramp-grey-200)',
  				'300': 'var(--ramp-grey-300)',
  				'400': 'var(--ramp-grey-400)',
  				'500': 'var(--ramp-grey-500)',
  				'600': 'var(--ramp-grey-600)',
  				'700': 'var(--ramp-grey-700)',
  				'800': 'var(--ramp-grey-800)',
  				'900': 'var(--ramp-grey-900)',
  				'1000': 'var(--ramp-grey-1000)'
  			},
  			border: 'hsl(var(--border) / <alpha-value>)',
  			input: 'hsl(var(--input))',
  			ring: 'hsl(var(--ring))',
  			node: {
  				DEFAULT: 'hsl(var(--node))',
  				foreground: 'hsl(var(--node-foreground))',
  				handle: 'hsl(var(--node-handle))',
  				border: 'hsl(var(--node-border))'
  			},
  			chart: {
  				'1': 'hsl(var(--chart-1))',
  				'2': 'hsl(var(--chart-2))',
  				'3': 'hsl(var(--chart-3))',
  				'4': 'hsl(var(--chart-4))',
  				'5': 'hsl(var(--chart-5))'
  			},
  			sidebar: {
  				DEFAULT: 'hsl(var(--sidebar-background))',
  				foreground: 'hsl(var(--sidebar-foreground))',
  				primary: 'hsl(var(--sidebar-primary))',
  				'primary-foreground': 'hsl(var(--sidebar-primary-foreground))',
  				accent: 'hsl(var(--sidebar-accent))',
  				'accent-foreground': 'hsl(var(--sidebar-accent-foreground))',
  				border: 'hsl(var(--sidebar-border))',
  				ring: 'hsl(var(--sidebar-ring))'
  			}
  		},
  		keyframes: {
  			'accordion-down': {
  				from: {
  					height: '0'
  				},
  				to: {
  					height: 'var(--radix-accordion-content-height)'
  				}
  			},
  			'accordion-up': {
  				from: {
  					height: 'var(--radix-accordion-content-height)'
  				},
  				to: {
  					height: '0'
  				}
  			}
  		},
  		animation: {
  			'accordion-down': 'accordion-down 0.2s ease-out',
  			'accordion-up': 'accordion-up 0.2s ease-out'
  		}
  	}
  },
  plugins: [
    tailwindcssAnimate,
    tailwindcssTypography
  ],
};

export default config;
