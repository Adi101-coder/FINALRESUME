Current Issues & Details
1. Job Discovery - Platforms Not Working
Platform	Status	Error
LinkedIn	0 available	0 fetched - needs login/session cookie
Indeed	6 available	6 fetched · 1 error (RSS deprecated)
Foundit	0 available	0 fetched · 1 error (timeout 30s)
Naukri	0 available	0 fetched · 1 error (406 Not Acceptable)
ZipRecruiter	0 available	0 fetched (selector issues)
Glassdoor	0 available	0 fetched (selector issues)
Remotive	✅ 9 available	9 fetched ✅
RemoteOK	❌ 0 available	0 fetched (API changed)
Arbeitnow	✅ 2 available	2 fetched ✅
Greenhouse	✅ 35 available	45 fetched ✅
Root causes: LinkedIn/Indeed/Naukri need login/session cookies; ZipRecruiter/Glassdoor/RemoteOK selectors broken; Foundit/RemoteOK timeout.
2. Auto-Apply Bot Not Working
Issue: "Approve All & Start Bot" → nothing happens
Root cause: Bot wasn't receiving review queue jobs; only accepted single job_url
Fixed: Updated /api/bot/start to accept jobs[] array from review queue
Status: Fixed locally, needs deploy
3. Score Calculation Issues
Problem: All scores too low (0 matches ≥60% initially)
Fixed: Lowered threshold to 60%, adjusted weights:
Relevance: 50% → 50%
Skills: 10% → 15% + base 15
Experience: 10% → 15% + base 25
Embedding: 15% → 15% × 1.2 + floor 20
Location: 10% → 5% + floor 50
Result: 17/84 matches ≥60% (was 0)
4. Platform Errors & Login Issues
Platform	Issue	Fix Status
LinkedIn	Needs login, no browser on Render	✅ Local works, hosted shows warning
Indeed	RSS deprecated, 403/406 errors	✅ Enhanced fallback + session cookie support
Foundit	Timeout 30s	⚠️ Increased timeout
Naukri	406 Not Acceptable	✅ Fixed Accept header
ZipRecruiter	0 results	⚠️ Selectors need update
Glassdoor	0 results	⚠️ Selectors need update
RemoteOK	0 results	⚠️ API changed
Foundit	Timeout	⚠️ Increased timeout
4. Login Flow Issues
Local: ✅ Opens YOUR Chrome, you log in, press "Login Done"
Hosted (Render): ❌ No display → shows warning "Run locally for login"
Fixed: Added isLocal detection, proper error polling
5. Review Queue / Auto-Apply Flow
Fixed: "+ Queue" buttons on match cards → populates review queue
Fixed: "Review & Approve" button passes reviewQueue to AutoApplyStep
Fixed: "Approve All & Start Bot" sends jobs[] from queue to bot
Bot: Now accepts jobs[] array in /api/bot/start
6. Dashboard / Application Status Board
Issue: Static mock data (Stripe, Vercel, Railway, Linear, Fly.io)
Need: Connect to real getApplications / getAnalytics APIs
Status: APIs exist but dashboard may not refresh properly
7. Frontend Build Issues
✅ Fixed syntax errors (% escaping, duplicate imports)
✅ Build passes locally
✅ Vercel deploy works (network hiccups only)
8. Deployment Status
Component	Status
Frontend (Vercel)	✅ https://frontend-kohl-seven-20.vercel.app (https://frontend-kohl-seven-20.vercel.app)
Backend (Render)	❌ Down (needs manual redeploy)
Local Backend	✅ http://localhost:8000 (http://localhost:8000)
Local Frontend	✅ http://localhost:3000 (http://localhost:3000)
Priority Fixes Needed:
Deploy backend fixes to Render (trigger redeploy)
Fix ZipRecruiter/Glassdoor/RemoteOK scrapers (selectors/API)
Fix Dashboard to show real applications
Add session cookie env vars for Indeed/LinkedIn/Foundit/Naukri on Render