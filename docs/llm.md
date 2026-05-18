# Databricks-Hosted LLM for GEMS Dashboard

The dashboard uses a lab-managed Databricks Model Serving endpoint for all LLM features:

- Explore page chart interpretation
- Modeling page model interpretation
- Chat page tool-calling assistant

The Chat page follows an aggregate-only policy. The LLM receives the data dictionary,
schema/table summaries, and validated aggregate query results. It must not receive raw
row-level data. To regenerate the editable first-draft data dictionary from Databricks:

```powershell
python tools/generate_data_dictionary.py
```

The dashboard uses the OpenAI Python SDK with a Databricks-compatible base URL.

## Required Azure App Settings

In Azure Portal:

```text
gems-dashboard -> Settings -> Environment variables -> App settings
```

Set:

```text
DATABRICKS_HOST=<workspace-host>
DATABRICKS_TOKEN=<Databricks PAT>
DATABRICKS_LLM_ENDPOINT=databricks-claude-haiku-4-5
```

`DATABRICKS_HOST` may be either:

```text
adb-xxxxxxxx.azuredatabricks.net
```

or:

```text
https://adb-xxxxxxxx.azuredatabricks.net
```

The code normalizes the host before calling:

```text
{DATABRICKS_HOST}/serving-endpoints/{DATABRICKS_LLM_ENDPOINT}/invocations
```

## Generate a Databricks PAT

In the Bovi-Analytics Azure Databricks workspace:

1. Open Databricks.
2. Click your user/profile menu.
3. Go to **User Settings**.
4. Open **Developer**.
5. Open **Access tokens**.
6. Click **Generate new token**.
7. Copy the token immediately.
8. Store it in Azure App Service as:

```text
DATABRICKS_TOKEN=<token>
```

Do not commit the token to GitHub or paste it into screenshots/chat.

## Model Switching

Switching models requires no code changes. Change only:

```text
DATABRICKS_LLM_ENDPOINT=<endpoint-name>
```

Available endpoints in the Bovi-Analytics workspace:

| Endpoint | Suggested use |
|---|---|
| `databricks-claude-opus-4-7` | Most capable |
| `databricks-claude-opus-4-6` | Highly capable |
| `databricks-claude-opus-4-5` | Highly capable |
| `databricks-claude-opus-4-1` | Highly capable |
| `databricks-claude-sonnet-4-6` | Balanced capability/cost |
| `databricks-claude-sonnet-4-5` | Balanced capability/cost |
| `databricks-claude-haiku-4-5` | Cheapest and fastest; default |
| `databricks-gpt-oss-120b` | Open model option |
| `databricks-gpt-oss-20b` | Smaller open model option |
| `databricks-qwen35-122b-a10b` | Qwen model option |

## Remove Legacy OpenAI Settings After Staging Verification

After verifying the Databricks endpoint on staging, remove these legacy settings from the `gems-dashboard` Azure App Service:

```text
OPENAI_API_KEY
OPENAI_CHAT_MODEL
OPENAI_MODEL
```

The dashboard keeps a local-development-only fallback to `OPENAI_API_KEY` when `DATABRICKS_LLM_ENDPOINT` is not set, but production should use Databricks.

## Startup Health Check

At dashboard startup, the app tries a small LLM request:

```text
ping
```

If reachable, logs show:

```text
[LLM] <endpoint> reachable.
```

If the endpoint check fails, the app logs a warning but does not crash:

```text
[LLM] WARNING: endpoint check failed: <error>
```

## Troubleshooting

### Endpoint check fails

Check:

- `DATABRICKS_HOST`
- `DATABRICKS_TOKEN`
- `DATABRICKS_LLM_ENDPOINT`
- Databricks token permissions
- serving endpoint name
- workspace network/access settings

### Tool-calling chat fails

The Chat page depends on OpenAI-compatible tool-calling support from the selected endpoint. If a model does not support tool calls reliably, switch to a stronger endpoint such as:

```text
databricks-claude-haiku-4-5
```
