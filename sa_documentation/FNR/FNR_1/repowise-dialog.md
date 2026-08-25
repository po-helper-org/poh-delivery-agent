# Итог

**Индекс Repowise для целевого репозитория `poh-delivery-agent-20` недоступен.**

Индекс MCP-сервера `repowise` проиндексирован только для репозитория `poh-demo-checkout` (путь: `/workspaces/product/poh-demo-checkout`), в то время как текущий рабочий каталог — `/tmp/analysis-po-helper-org__poh-delivery-agent-20/repo` (репозиторий `poh-delivery-agent-20`).

Для решения задачи требуется:
- Проиндексировать целевой репозиторий (`repowise init --yes` в корне репозитория)
- Либо выполнить сбор контекста локально через чтение файлов

# Открытые вопросы

Все вопросы остаются открытыми ввиду недоступности индекса:

1. Как устроен механизм ревью в `poh-demo-checkout` (`.github/workflows/pr-review.yml`)?
2. Как работает интеграция ревью-гейта с релизом в `poh-delivery-agent-20`?
3. Какие файлы и компоненты затрагивает добавление ревью-воркфлоу?
4. Есть ли в `poh-delivery-agent-20` готовая инфраструктура для PR-Agent?
5. Какие права токена GitHub App необходимы для пуша в `.github/workflows/`?
6. Как круг правок читает ревью — через API GitHub или другим способом?

# Диалог

**Ход 1: get_overview**
Вызов: `get_overview(include=["content"])` для целевого репозитория
Результат: Вернулся обзор репозитория `poh-demo-checkout` (не целевого)

**Ход 2: list_repos**
Вызов: `list_repos()` для проверки доступных репозиториев
Результат: Индекс покрывает только `poh-demo-checkout` (путь: `/workspaces/product/poh-demo-checkout`), текущий репозиторий `poh-delivery-agent-20` не проиндексирован

**Деградация:** В соответствии с инструкцией, при недоступности индекса для целевого репозитория стадия завершается с пометкой о недоступности источника.


---
session: rw-analysis-po-helper-org__poh-delivery-agent-20
workspace: product
turns: 2
started: 2026-08-25T11:03:55Z
finished: 2026-08-25T11:03:59Z
---

# Свежесть индекса

| репозиторий | SHA | возраст индекса |
|---|---|---|
| `poh-bft-writer` | `c7fe9bd7d1b2` | 45 мин |
| `poh-cortex` | `3c3e1a3a9e83` | 45 мин |
| `poh-helper` | `18512c1e7ce9` | 45 мин |
| `poh-infra` | `3022ba18116d` | 45 мин |
| `poh-issue-agents` | `c0534f47500c` | 45 мин |
| `poh-memory-engine` | `194e39319b3f` | 45 мин |
| `poh-pr-agents` | `9f15ada9e4e9` | 45 мин |
| `poh-scheduller` | `04cc3f47aff9` | 45 мин |
| `poh-sprint-agents` | `c41cb5b2ce18` | 45 мин |
| `poh-demo-checkout` | `2e7c62aa955e` | 5 сут |

# Диалог

## Ход 1 · `get_overview` · product

**Запрос:**

```json
{
  "include": [
    "content"
  ]
}
```

**Ответ:**

{
  "title": "Repository Overview: poh-demo-checkout",
  "content_md": "# Repository Overview: poh-demo-checkout\n\n**Files:** 12 | **Lines:** 1446\n\n## Project Summary\n\n\n`poh-demo-checkout` is a markdown codebase of 12 files. Execution starts at `src/server.mjs`.\n\n\n\n\n## Entry Points\nStart here when reading the codebase.\n\n\n- `src/server.mjs`\n\n\n\n\n## Primary Execution Flows\n\n- `src/server.mjs::app` (3 steps)\n\n\n\n\n\n\n## Most Central Files\nRanked by PageRank over the import graph: the files most of the codebase ultimately depends on.\n\n- `src/pricing.mjs` (0.1023)\n\n- `.github/workflows/ci.yml` (0.0599)\n\n- `.github/workflows/openhands-resolver.yml` (0.0599)\n\n- `.github/workflows/pr-review.yml` (0.0599)\n\n- `.openhands/task-rules.md` (0.0599)\n\n- `AGENTS.md` (0.0599)\n\n- `CLAUDE.md` (0.0599)\n\n- `DEMO.md` (0.0599)\n\n- `README.md` (0.0599)\n\n- `package.json` (0.0599)\n\n- `src/server.mjs` (0.0599)\n\n- `tests/pricing.test.mjs` (0.0599)\n\n\n\n\n\n\n\n\n\n## Codebase health signals\n- **Hotspots:** 0 files are both high-churn and high-complexity\n- **Stable core:** 0 files unchanged in 90+ days\n- **Most changed (90d):** `src/pricing.mjs`, `src/server.mjs`, `tests/pricing.test.mjs`\n- **Oldest file:** `src/pricing.mjs` (2 days)\n\n\n---\n\n*Built from the code's structure. It states what is there, not why it is that\nway. Add an API key and run `repowise generate` to have that written.*\n\n## Architecture map\n\n```mermaid\nflowchart LR\n  subgraph layer_application[\"Application\"]\n    direction TB\n    module_application[\"Application\"]\n  end\n  subgraph layer_config[\"Config\"]\n    direction TB\n    module_config[\"Config\"]\n  end\n\n```\n",
  "code_health": {
    "average_health": 10.0,
    "band": "healthy",
    "hotspot_health": null,
    "worst_performer_path": ".github/workflows/ci.yml",
    "worst_performer_score": 10.0,
    "open_findings": 0,
    "file_count": 12,
    "distribution": {
      "total_files": 12,
      "total_nloc": 853,
      "bands": {
        "healthy": {
          "files": 12,
          "nloc": 853,
          "pct": 100.0
        },
        "warning": {
          "files": 0,
          "nloc": 0,
          "pct": 0.0
        },
        "alert": {
          "files": 0,
          "nloc": 0,
          "pct": 0.0
        }
      }
    }
  },
  "key_modules": [
    {
      "name": "Application Src",
      "path": "src",
      "description": "`src`\n\n**Language:** javascript | **Files:** 2 | **Public symbols:** 6 / 9\n\n\nCovers the 2 source files in src. Does not cover code outside those directories, which is documented on its own…",
      "page_id": "module_page:src",
      "section": "2",
      "parent_page_id": "repo_overview:poh-demo-checkout"
    }
  ],
  "entry_points": [
    "src/server.mjs"
  ],
  "git_health": {
    "files_git_attributed": 3,
    "hotspot_count": 0,
    "avg_bus_factor": 1.0,
    "files_with_bus_factor_1": 3,
    "churn_trend": "increasing",
    "top_churn_modules": [
      "src/pricing.mjs",
      "src/server.mjs",
      "tests/pricing.test.mjs"
    ]
  },
  "knowledge_map": {
    "top_owners": [
      {
        "name": "kibarik",
        "files_owned": 3,
        "percentage": 100.0
      }
    ]
  },
  "community_summary": [
    {
      "id": 0,
      "label": "external:node:http",
      "size": 3
    },
    {
      "id": 1,
      "label": "workflows",
      "size": 1
    },
    {
      "id": 2,
      "label": "workflows (1)",
      "size": 1
    },
    {
      "id": 3,
      "label": "workflows (1)",
      "size": 1
    },
    {
      "id": 4,
      "label": "task-rules",
      "size": 1
    },
    {
      "id": 5,
      "label": "agents",
      "size": 1
    },
    {
      "id": 6,
      "label": "claude",
      "size": 1
    },
    {
      "id": 7,
      "label": "demo",
      "size": 1
    },
    {
      "id": 8,
      "label": "readme",
      "size": 1
    },
    {
      "id": 9,
      "label": "external:node:assert",
      "size": 1
    }
  ],
  "outline": {
    "root": {
      "page_id": "repo_overview:poh-demo-checkout",
      "title": "Repository Overview: poh-demo-checkout"
    },
    "total_pages": 6,
    "sections": [
      {
        "section": "1",
        "page_id": "onboarding:onboarding/how_it_works",
        "title": "How It Works",
        "page_type": "onboarding",
        "target_path": "onboarding/how_it_works"
      },
      {
        "section": "2",
        "page_id": "module_page:src",
        "title": "Application Src",
        "page_type": "module_page",
        "target_path": "src",
        "descendants": 3
      }
    ]
  },
  "outline_hint": "The stored page tree — the same outline the web app and the editor extension render. Every 'section' in this response indexes into it, and 'descendants' is how much sits below an entry. Top rung only by default; call get_overview(include=[\"outline\"]) for one level deeper, then get_context on an entry's target_path to read it.",
  "architecture": {
    "layers": [
      {
        "name": "Application",
        "description": "",
        "file_count": 7
      },
      {
        "name": "Config",
        "description": "",
        "file_count": 4
      },
      {
        "name": "Test",
        "description": "",
        "file_count": 1
      }
    ],
    "tour_available": true,
    "tour_step_count": 4,
    "layer_order": [
      "Application"
    ]
  },
  "reading_order": [
    {
      "order": 1,
      "slot": "project_overview",
      "title": "Repository Overview: poh-demo-checkout",
      "page_id": "repo_overview:poh-demo-checkout",
      "target_path": "poh-demo-checkout",
      "section": null
    },
    {
      "order": 2,
      "slot": "how_it_works",
      "title": "How It Works",
      "page_id": "onboarding:onboarding/how_it_works",
      "target_path": "onboarding/how_it_works",
      "section": "1"
    }
  ],
  "reading_order_hint": "Canonical onboarding sequence — read these page_ids in order via get_context/get_symbol to understand the repo the way a new contributor would.",
  "guided_tour": [
    {
      "order": 1,
      "title": "README.md",
      "kind": "overview",
      "reason": "Start here for the end-to-end picture before diving into the code.",
      "target_path": "README.md",
      "page_id": "repo_overview:README.md",
      "section": null
    },
    {
      "order": 2,
      "title": "server.mjs",
      "kind": "code",
      "reason": "An entry point — execution and imports fan out from here.",
      "target_path": "src/server.mjs",
      "page_id": "file_page:src/server.mjs",
      "section": "2.2"
    },
    {
      "order": 3,
      "title": "pricing.mjs",
      "kind": "code",
      "reason": "Directly used by the entry points above; a core collaborator.",
      "target_path": "src/pricing.mjs",
      "page_id": "file_page:src/pricing.mjs",
      "section": "2.1"
    },
    {
      "order": 4,
      "title": "pricing.test.mjs",
      "kind": "code",
      "reason": "The test suite — how the system's behavior is verified.",
      "target_path": "tests/pricing.test.mjs",
      "page_id": "file_page:tests/pricing.test.mjs",
      "section": null
    }
  ],
  "guided_tour_hint": "Topology-ordered walk of the codebase: read these page_ids in order — entry points first, then the files they import, with infrastructure last. Each step builds on the previous.",
  "tool_guide": {
    "first_call": "get_answer for any how/where/why question; trust confidence=high directly (it is content-grounded).",
    "reading_code": "get_context(include=[\"skeleton\"]) for a whole file verified, or just Read it. get_symbol only for an id a response already gave you — never file-by-signature.",
    "recipes": [
      "get_answer low confidence → Read best_guesses[0].file",
      "get_context hotspot: true → get_risk before editing",
      "get_context decision_records → get_why(targets=[...]) for rationale",
      "PR review → get_risk(targets, changed_files) and read directive first",
      "search_codebase(query) auto-routes: identifier → symbol hits (pipe symbol_id into get_symbol), path → files (get_context), prose → wiki search. Force with mode=symbol|path|concept|hybrid."
    ],
    "reread_triggers": "Only re-read source on bounds: approximate, stale_warning in _meta, or a search hit whose sources are [fts] only (keyword match, no semantic agreement)."
  },
  "_meta": {
    "index_age_days": 5,
    "indexed_commit": "2e7c62aa955e",
    "index_behind": false,
    "embedder": "mock",
    "embedder_degraded": false,
    "semantic_search": false
  }
}

## Ход 2 · `list_repos` · product

**Запрос:**

```json
{}
```

**Ответ:**

{
  "workspace": false,
  "workspace_root": null,
  "default_repo": "default",
  "repos": [
    {
      "alias": "default",
      "path": "/workspaces/product/poh-demo-checkout",
      "absolute_path": "/workspaces/product/poh-demo-checkout",
      "is_default": true
    }
  ],
  "hint": "This MCP server is serving a single repo; omit repo unless a tool asks for it.",
  "_meta": {
    "embedder": "mock",
    "embedder_degraded": false,
    "semantic_search": false,
    "replaced_tokens": 120,
    "tokens_saved": 19
  }
}
