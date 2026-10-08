# Spend: connecting your AI provider accounts

The Spend page already counts what llamawatch itself and your coding tools use. This guide covers the other half: money spent through your own provider accounts by scripts and apps that llamawatch never sees. Connect an account on the Spend page (Connected providers) and its spend joins the same ledger.

All keys are stored encrypted in `config.local.json` on the machine running llamawatch. Only the owner (the machine itself, or a signed-in session) can add or remove one. The page never receives a key back. Each account is asked at most once an hour. If a provider fails to answer, the page keeps working and shows the reason next to that provider.

## Which providers work

| Provider | What you get | Key you paste | History | Needs |
|---|---|---|---|---|
| OpenAI | Tokens, requests and cost per day, per model | Admin key | 35 days on connect | An organisation you administer |
| Anthropic API | Tokens and cost per day, per model | Admin key (`sk-ant-admin...`) | 35 days on connect | An organisation account (not an individual one) |
| OpenRouter | Cost per day | Normal API key | From the day you connect | Only counts that key's own use |
| DeepSeek | Spend per day, from balance drops | Normal API key | From the day you connect | Prepaid balance |
| Kimi (Moonshot) | Spend per day, from balance drops | Normal API key | From the day you connect | Key from platform.moonshot.ai |
| xAI (Grok) | Spend per day, from prepaid balance drops | Management key + team ID | From the day you connect | Prepaid credit. Not yet tried against a live account |
| SiliconFlow | Spend per day, from balance drops | Normal API key | From the day you connect | Key from siliconflow.com |
| Novita AI | Spend per day, from balance drops | Normal API key | From the day you connect | Prepaid balance |
| DeepInfra | Spend per day, from the month's running total | Normal API key | From the day you connect | |

Three kinds of answer, and the Spend page labels each row with its kind:

- **Usage report.** The provider keeps a per-day record and gives it to us. Exact, with history.
- **Balance drops.** The provider only says how much money is left. llamawatch notes the balance each time it asks and counts every drop as spend for that day. A top-up counts as nothing. History starts the day you connect, and tokens are not known, only money. If nobody opens the Spend page for a few hours, the drop in that gap lands on the day it was noticed.
- **Monthly total.** The provider gives this month's running total. Each rise is spend for that day.

## Providers with no usage data to read

These providers offer no way to read spend with an API key, so llamawatch cannot connect to them. Spend through them appears only if it goes through llamawatch (Research, or a coding tool) and you type their price in the Prices table.

Groq, Mistral, Together, Cohere, Perplexity, Cerebras, Google Gemini (AI Studio key), Zhipu, MiniMax, Hugging Face, Replicate, Fireworks, Alibaba DashScope (Model Studio).

Some of these show spend in their own web console. They just do not offer it to a key. If a provider adds a balance or usage call, it can be added here.

## The big clouds (not supported yet)

AWS Bedrock (Cost Explorer), Azure OpenAI (Cost Management) and Google Vertex AI (billing export) report spend through the cloud's own billing system, which needs cloud credentials rather than an API key. They are not connected yet.

## No double counting

- Research runs on a model whose address belongs to a connected provider are left out of the ledger, because the provider's own figure already includes them.
- If you connect **Anthropic API** and also typed prices for Claude models in the Prices table, remove those prices. Claude Code run through an API key would be counted twice. (Claude Code on a subscription costs nothing extra and needs no price.)
- If you connect OpenAI or another provider and also have a typed price for the same model, the typed price only applies to llamawatch's own tokens. Provider rows are named after the provider (for example `OpenAI`), so they never collide.

## How to connect each one

On the Spend page, open **Connected providers**, press **Connect** beside the provider, paste the key and press **Test and save**. llamawatch asks the provider once. If the provider rejects the key, nothing is saved and the reason is shown. **Check now** asks again straight away. **Disconnect** removes the key and forgets the history that provider gave.

### OpenAI

1. Open platform.openai.com, then Settings, Organization, Admin keys.
2. Create a new admin key. A normal API key is refused (error 403).
3. Paste it. llamawatch reads `GET /v1/organization/usage/completions` and `GET /v1/organization/costs`.

Cost is the account's total for each day. Per-model cost is not split out because the cost report names line items differently from the usage report. Per-model tokens are shown.

### Anthropic API

1. Open platform.claude.com, then Settings, Admin keys. You need an organisation (Settings, Organization); individual accounts cannot make one.
2. Create an admin key (`sk-ant-admin...`). Workspace API keys do not work.
3. Paste it. llamawatch reads `/v1/organizations/usage_report/messages` and `/v1/organizations/cost_report`.

Anthropic reports cost in cents; llamawatch converts to dollars. Priority Tier cost is not in the cost report, so it is missing from the money total (its tokens still show).

### OpenRouter

1. openrouter.ai, Settings, Keys, create a key.
2. Paste it. llamawatch reads `GET /api/v1/key` and takes that key's usage for the current day. Days before you connected are not known, and other keys on the account are not included.

### DeepSeek

1. platform.deepseek.com, API keys, create a key.
2. Paste it. llamawatch reads `GET /user/balance`.

The balance is in the account's own currency (shown as `$` or `¥`).

### Kimi (Moonshot)

1. platform.moonshot.ai, API keys, create a key. Keys from the Chinese platform are separate and will not work here.
2. Paste it. llamawatch reads `GET /v1/users/me/balance` (available balance, in dollars).

### xAI (Grok)

1. console.x.ai, create a **management key** (Settings, Management keys) and note your **team ID**.
2. Paste both. llamawatch reads `GET /v1/billing/teams/{team_id}/prepaid/balance` from management-api.x.ai.

This one has not been tried against a live account. If it fails for you, the error shows on the card.

### SiliconFlow

1. cloud.siliconflow.com, API keys, create a key.
2. Paste it. llamawatch reads `GET /v1/user/info` and uses the total balance (dollars).

### Novita AI

1. novita.ai, Key management, create a key.
2. Paste it. llamawatch reads `GET /openapi/v1/billing/balance/detail`. The provider counts in ten-thousandths of a dollar; llamawatch converts.

### DeepInfra

1. deepinfra.com, Dashboard, API keys, create a key.
2. Paste it. llamawatch reads `GET /payment/usage?from=current` and uses the month's total cost (reported in cents).

## When something looks wrong

| You see | Meaning |
|---|---|
| `HTTP 401: key rejected` | The key is wrong, revoked or from another region. |
| `HTTP 403: key not allowed (wrong key type?)` | You pasted a normal key where an admin or management key is needed. |
| `HTTP 429: rate limited, will retry` | Asked too often. It tries again within the hour. |
| `cannot reach provider` | No internet from the machine, or the provider is down. |
| A provider shows $0 | Balance providers show nothing until the balance has dropped once after you connected. |

## For developers

The adapters are in `llamawatch/spend_providers.py`, one function per provider, each returning a `days`, `balance` or `cumulative` answer. The stored history is `providers.json` beside the daily ledger. To add a provider, write one `fetch_<name>` function, add it to `PROVIDERS`, and add a test with a mocked answer in `tests/test_spend.py`.
