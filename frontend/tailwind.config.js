/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Brand accent: a single, restrained navy-blue in the spirit of mature legal
        // and enterprise tooling (Thomson Reuters / Microsoft 365). Deliberately
        // desaturated so it reads as trustworthy infrastructure, never as a flashy
        // startup gradient. Used sparingly for primary actions and active states.
        brand: {
          50: '#f1f5fa',
          100: '#e1ebf4',
          200: '#c3d6e8',
          300: '#97b6d6',
          400: '#638fbd',
          500: '#3f6ea3',
          600: '#2f5688', // primary actions
          700: '#284a73', // hover / strong
          800: '#243f60',
          900: '#213651',
          950: '#152236',
        },
        // Neutral "ink" ramp for text + hairline borders. Cool, near-neutral grays
        // (slightly warmer than pure slate) give the calm, document-first canvas.
        ink: {
          50: '#f7f8fa',
          100: '#eef0f3',
          200: '#e2e5ea',
          300: '#cbd0d8',
          400: '#9aa2af',
          500: '#6b7480',
          600: '#4d5560',
          700: '#3a414b',
          800: '#262b33',
          900: '#171a1f',
        },
        // Grounded source-span annotation palette — meaning-bearing, slightly deepened
        // from the previous neon tones so highlights read as careful legal markup.
        span: {
          party: '#1d4ed8', // blue   — obligated party
          action: '#15803d', // green  — action / obligation verb phrase
          deadline: '#b45309', // bronze — deadline / time period
        },
        // Risk / priority semantics for findings and obligations. Muted, professional
        // tones (no neon) so severity is legible without alarm-fatigue.
        risk: {
          critical: '#b42318',
          high: '#b54708',
          medium: '#9a7314',
          low: '#475467',
        },
        review: '#b54708', // amber-bronze treatment for requires_review items
      },
      fontFamily: {
        sans: [
          'Inter',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
        mono: [
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'Consolas',
          'Liberation Mono',
          'monospace',
        ],
      },
      fontSize: {
        // Tightened, document-oriented scale with sensible line-heights.
        '2xs': ['0.6875rem', { lineHeight: '1rem' }], // 11px labels
        xs: ['0.75rem', { lineHeight: '1.1rem' }],
        sm: ['0.8125rem', { lineHeight: '1.35rem' }], // 13px UI base
        base: ['0.875rem', { lineHeight: '1.5rem' }], // 14px
        // Reading size used for contract body text.
        reading: ['0.9375rem', { lineHeight: '1.65rem' }], // 15px
        lg: ['1rem', { lineHeight: '1.5rem' }],
        xl: ['1.125rem', { lineHeight: '1.6rem' }],
        '2xl': ['1.375rem', { lineHeight: '1.85rem' }],
      },
      // Tamed radius scale — no oversized pills on structural surfaces. Class names
      // are preserved so existing markup keeps working, just at calmer values.
      borderRadius: {
        none: '0',
        sm: '0.1875rem', // 3px
        DEFAULT: '0.25rem', // 4px
        md: '0.3125rem', // 5px
        lg: '0.375rem', // 6px  (cards)
        xl: '0.5rem', // 8px
        '2xl': '0.625rem', // 10px
        '3xl': '0.875rem',
        full: '9999px',
      },
      boxShadow: {
        // Conservative elevation: hairline-soft, never glowy. Borders do most of the
        // separation work (GitHub / Linear style); shadows only hint at layering.
        xs: '0 1px 1px 0 rgb(16 24 40 / 0.04)',
        card: '0 1px 2px 0 rgb(16 24 40 / 0.05)',
        'card-hover': '0 2px 4px -1px rgb(16 24 40 / 0.08), 0 1px 2px -1px rgb(16 24 40 / 0.05)',
        panel: '0 1px 3px 0 rgb(16 24 40 / 0.06), 0 1px 2px -1px rgb(16 24 40 / 0.04)',
        focus: '0 0 0 3px rgb(47 86 136 / 0.25)',
      },
    },
  },
  plugins: [],
};
