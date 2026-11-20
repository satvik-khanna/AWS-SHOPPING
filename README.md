# AWS-SHOPPING
Personal shopping agent that finds niche products and orders them only after you approve.
Japan Shopping Agent

Problem: Japanese marketplaces like Mercari and Yahoo Auctions carry items you can’t get elsewhere. But the listings are in Japanese, many sellers won’t ship abroad, and the real cost is hidden across proxy fees, shipping and duties.

What it does: You describe what you want (“used Fujifilm X100V under $900, shipped to California”). The agent then:

Recalls your preferences and past purchases.
Searches Japanese marketplaces, translating and extracting details from each listing.
Ranks the best matches and works out the full landed cost of each, citing the fee and duty rules behind every number.
Fills the cart on a proxy service (Buyee) and stops for your approval before anything is paid.

Why it fits the challenge: it uses trusted context to decide, takes a real action (the checkout), and shows the reasoning trail.

Stack

Layer	Tool	Role
Trusted context	Senso	Fee schedules, shipping rates, duty rules, banned items, user budget and sizes. Backs every cost line.
Task models	Superlinked SIE	Translation, field extraction from listings and photos, reranking
Memory	Cognee	Past purchases, rejected sellers, condition standards
Safety testing	Firedrill	Simulated checkout runs proving the agent stops at approval
Browser automation	Stagehand or Browser Use, on Browserbase	Navigates Buyee and fills the cart
Reasoning	Claude API	Final pick and explanation
Observability	Tokens&	Request timing and error traces
Optional	Statsig/Amplitude, Arcee	A/B two ranking strategies; cheap vs frontier model comparison

Sponsor prizes targeted: Senso, Superlinked (both prizes), Cognee and Firedrill, plus Statsig/Amplitude and Arcee if time allows.

Demo: request → recalled preferences → translated and ranked listings → cited landed-cost breakdown → cart filled and paused for approval, with the Firedrill report on screen.
