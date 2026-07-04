---
name: Modernist Engineering
colors:
  surface: '#fff8f4'
  surface-dim: '#e0d9d4'
  surface-bright: '#fff8f4'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#faf2ee'
  surface-container: '#f4ece8'
  surface-container-high: '#eee7e2'
  surface-container-highest: '#e8e1dd'
  on-surface: '#1e1b19'
  on-surface-variant: '#4e453c'
  inverse-surface: '#33302d'
  inverse-on-surface: '#f7efeb'
  outline: '#80756b'
  outline-variant: '#d1c4b8'
  surface-tint: '#725a3d'
  primary: '#725a3d'
  on-primary: '#ffffff'
  primary-container: '#bfa17f'
  on-primary-container: '#4d381d'
  inverse-primary: '#e1c19d'
  secondary: '#5f5e5e'
  on-secondary: '#ffffff'
  secondary-container: '#e2dfde'
  on-secondary-container: '#636262'
  tertiary: '#5e5e5e'
  on-tertiary: '#ffffff'
  tertiary-container: '#a7a6a6'
  on-tertiary-container: '#3b3c3c'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffddb8'
  primary-fixed-dim: '#e1c19d'
  on-primary-fixed: '#291803'
  on-primary-fixed-variant: '#594327'
  secondary-fixed: '#e5e2e1'
  secondary-fixed-dim: '#c8c6c5'
  on-secondary-fixed: '#1c1b1b'
  on-secondary-fixed-variant: '#474746'
  tertiary-fixed: '#e4e2e2'
  tertiary-fixed-dim: '#c7c6c6'
  on-tertiary-fixed: '#1b1c1c'
  on-tertiary-fixed-variant: '#464747'
  background: '#fff8f4'
  on-background: '#1e1b19'
  surface-variant: '#e8e1dd'
typography:
  headline-lg:
    fontFamily: Playfair Display
    fontSize: 48px
    fontWeight: '700'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Playfair Display
    fontSize: 32px
    fontWeight: '600'
    lineHeight: '1.2'
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Playfair Display
    fontSize: 24px
    fontWeight: '500'
    lineHeight: '1.3'
  headline-lg-mobile:
    fontFamily: Playfair Display
    fontSize: 32px
    fontWeight: '700'
    lineHeight: '1.2'
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.6'
  label-caps:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: '1'
    letterSpacing: 0.1em
  data-mono:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: '1.4'
    letterSpacing: -0.01em
spacing:
  unit: 4px
  gutter: 24px
  margin-mobile: 16px
  margin-desktop: 48px
  section-gap: 80px
---

## Brand & Style
The design system embodies **Modernist Elegance**, drawing inspiration from architectural blueprints and high-end structural galleries. It is tailored for high-stakes structural engineering, where precision meets prestige. 

The aesthetic is characterized by:
- **Architectural Minimalism:** Functional layouts that prioritize structural clarity and negative space.
- **Sophisticated Professionalism:** A visual tone that evokes trust, longevity, and intellectual rigor.
- **Tactile Luxury:** Subtle textures (fine linen, brushed aluminum) that provide depth without noise.
- **Precision Detailing:** Ultra-thin lines and hair-line strokes that mimic technical drawings.

## Colors
The palette is a restricted monochromatic light scheme with a single metallic accent.
- **Backgrounds:** Use Alabaster White (#FDFDFD) for primary canvases and Very Light Pearl Gray (#F6F6F6) for sidebar panels and data-heavy containers.
- **Typography:** Headings utilize Near Black (#1A1A1A) for maximum authority. Body text and technical notations use Soft Graphite (#5F5F5F) to reduce visual strain while maintaining legibility.
- **Accents:** Champagne Gold (#BFA17F) is reserved strictly for primary calls to action, active indicators, and critical highlights.
- **Structural Lines:** Cool Silver (#E0E0E0) is used for all dividers and component boundaries.

## Typography
The system employs a high-contrast typographic pairing:
- **Playfair Display:** Used for headlines and title sections. It brings a literary, editorial quality to the platform.
- **Inter:** Used for all functional UI elements, technical data, and body copy. It ensures clarity and readability at small scales.
- **Hierarchy:** Use `label-caps` for section headers above data tables or input groups to create an architectural "blueprint" feel.

## Layout & Spacing
The layout follows a **Fixed Grid** philosophy on desktop to ensure structural integrity of technical drawings and data. 
- **Desktop:** 12-column grid with a max-width of 1440px. 
- **Rhythm:** An 8px/4px base unit system. Generous padding (48px+) is used between major content sections to emphasize the "Minimalist" aesthetic.
- **Separation:** Rely on thin 1px lines (#E0E0E0) for logical grouping rather than colored backgrounds or heavy shadows.

## Elevation & Depth
Depth is created through transparency and texture rather than traditional drop shadows.
- **Floating Panels:** Diagnostic panels and context menus use a backdrop blur (20px) with 80% opacity Alabaster White. 
- **Tonal Layering:** Deep content hierarchy is shown by transitioning from linen-textured white (#FDFDFD) to brushed-aluminum pearl (#F6F6F6).
- **Outlines:** All components use a 1px solid border. Active states are denoted by a color shift to Champagne Gold or a 2px stroke weight.

## Shapes
In alignment with structural engineering motifs, the design system utilizes **Sharp (0px)** corners for almost all UI elements. 
- **Buttons and Inputs:** Strictly rectangular.
- **Exceptions:** Very small data tags or status indicators may use a 2px radius only to differentiate them from interactive buttons.
- **Lines:** Horizontal and vertical lines must be crisp 1px strokes.

## Components
- **Buttons:** Primary buttons are Champagne Gold with Near Black text. Secondary buttons are transparent with a 1px Near Black border. No shadows.
- **Inputs:** Underline-only or 1px bordered boxes. Focus states transition the border to Champagne Gold. Labels are always `label-caps`.
- **Cards:** Defined by a 1px border (#E0E0E0). Header sections of cards should have a pearl gray background to separate from the body.
- **Chips/Status:** Minimalist text with a small square prefix icon. Avoid pill shapes.
- **Floating Menus:** Use the Glassmorphism effect with a subtle 1px border. Ensure the "Micro-brushed aluminum" texture is applied to any pearl gray surfaces within panels for a tactile feel.
- **Technical Readouts:** Use the `data-mono` type style. Tabular data should have no vertical lines, only thin horizontal row separators.