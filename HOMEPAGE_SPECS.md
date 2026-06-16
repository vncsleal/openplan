# OpenPlan Landing Page — Specs

**Site:** openplan.cc

## Stack

- **Framework:** Astro (5 or 6, either works for static sites)
- **Deployment:** Cloudflare Pages (connected to a GitHub repo, auto-deploys on push)
- **Analytics:** Cloudflare Web Analytics (privacy-first, free, no cookie banner)
- **Domain:** openplan.cc — point DNS to Cloudflare Pages

## Pages

Single landing page only. No blog, no dashboard, no docs.

## Content

### Hero Section

**Headline:** Waze for AI agents planning

**Subtitle:** An MCP server that improves your AI agent's project cost estimates by learning from every agent that uses it.

**CTA buttons:**
- "Install from Smithery" (primary) — links to https://smithery.ai/server/@vncsleal/openplan
- "View on GitHub" (secondary) — links to https://github.com/vncsleal/openplan

### How It Works (3 steps)

1. **Install** — Add `uvx openplan-mcp` to your agent's MCP config. One line, no setup.
2. **Plan** — Your agent uses `start` and `complete` to track projects. Costs, phases, and outcomes are recorded automatically.
3. **Improve** — Anonymized cost data pools across all users. Every agent's estimates improve over time — like Waze, but for AI project planning.

### Audience

- Developers using AI coding agents (OpenCode, Claude Desktop, Cursor, VS Code Copilot)
- Teams running multiple AI agents who want consistent planning and cost tracking

### Pricing

**Free**
- Global calibration pool
- Rate-limited sync (100 events/min)
- 30-day retention

**Pro — $9/mo**
- Unlimited sync
- Forever retention
- Per-user historical trends

> Note: Enterprise tier is not yet available. Do not include it on the page.

### Footer

- Links: GitHub, PyPI (`openplan-mcp`), Smithery
- Privacy note: "Only `{project_type, action, cost}` is collected. No source code, no PII."

## Design Direction

- Dark theme preferred (developer audience)
- One page, no routing
- Animations welcome: scroll-based reveals, gradient backgrounds, subtle interactive elements
- Must be readable without JavaScript (the animations degrade gracefully)
- No tracking scripts beyond Cloudflare Analytics

## Assets & Links

| What | Value |
|------|-------|
| PyPI badge | `https://img.shields.io/pypi/v/openplan-mcp?color=blue` |
| GitHub repo | `https://github.com/vncsleal/openplan` |
| PyPI package | `openplan-mcp` |
| Smithery | `https://smithery.ai/server/@vncsleal/openplan` |
| API docs | point to GitHub README for now |

## Build & Deploy

```bash
# Create the project
npm create astro@latest

# Add Cloudflare adapter
npx astro add cloudflare

# Build
npm run build

# Deploy — connect the GitHub repo to Cloudflare Pages
# Cloudflare auto-detects Astro and uses the correct build command
```

## DNS

Point `openplan.cc` to Cloudflare Pages:
- Cloudflare Pages provides a `pages.dev` domain after first deploy
- Add a custom domain: `openplan.cc` in the Cloudflare Pages dashboard
- Cloudflare automatically handles the DNS and SSL
