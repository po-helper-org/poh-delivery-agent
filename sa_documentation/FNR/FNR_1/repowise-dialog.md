# Итог

1. **Целевой репозиторий — `po-helper-org/poh-delivery-agent`** (репозиторий в текущей директории). Это релиз-менеджер контура производства, отдельный репозиторий с отдельным релизным циклом. Модуль `poh_delivery/` содержит основную логику.

2. **Ключевой файл для выкатки — `poh_delivery/prod.py`**. Там класс `DockerProd` реализует выкатку на прод-контур (контейнер на том же хосте, что и воркер) и проверки живого сервиса. Методы:
   - `deploy()` — поднятие контейнера из состояния репозитория на нужном SHA
   - `verify()` — прогон проверок из `.delivery/checks.json` целевого репозитория
   - `_wait_ready()` — ожидание, пока сервис ответит на health_path
   - `current_sha()` — текущий SHA на проде

3. **Ключевой файл для воркфлоу — `poh_delivery/workflow.py`**. Класс `DeliveryRelease` (строки 84-491) реализует семь шагов релиза. Критичное место — строки 213-252, где происходит:
   - `delivery_merge` — влитие PR
   - `delivery_read_checks` — перечитывание проверок из влитого состояния
   - `delivery_deploy` — выкатка на прод-контур
   - `delivery_verify` — проверки живого сервиса
   - Решение: если `deployed.ok and not failed` → шаг успешен, иначе откат

4. **Активности — `poh_delivery/activities.py`**. Все внешние эффекты (merge, deploy, verify, revert и т.д.) реализованы как отдельные активности Temporal. Новая активность для окна наблюдения должна быть добавлена сюда и в список `ALL`.

5. **Протоколы — `poh_delivery/ports.py`**. `ProdPort` определяет интерфейс к прод-контуру (deploy, verify, current_sha). Для наблюдения нужны будут новые методы.

6. **Модель — `poh_delivery/model.py`**. Все dataclass'ы, которые ездят через Temporal:
   - `StepOutcome` — результат шага (ok, merged_sha, deployed, checks, rolled_back, detail)
   - `DeployResult` — результат выкатки (ok, sha, detail, url)
   - `CheckResult` — результат проверки (name, ok, detail)
   - `CheckSpec` — спецификация проверки из `.delivery/checks.json`

7. **Рендеринг отчётов — `poh_delivery/render.py`**. Метод `report_md()` (строки 146-205) формирует отчёт релиза после выкатки. Там есть раздел "Проверки по шагам" (строки 178-187) — именно туда нужно добавить информацию о наблюдении.

8. **Тесты — `tests/test_workflow.py`**. Проверяют порядок внешних эффектов. Критичная проверка — строка 209: порядок `["merge:5", "checks:merged5", "deploy:merged5", "verify"]`. После внедрения окна наблюдения порядок станет `["merge:5", "checks:merged5", "deploy:merged5", "observe", "verify"]`.

9. **Тесты прод-контура — `tests/test_prod.py`**. Проверяют разбор `.delivery/checks.json` и вердикты по ответу живого сервиса. Новые тесты нужны для:
   - Проверки, что контейнер жив всё окно
   - Проверки, что контейнер не перезапускался
   - Провала, если контейнер умер во время окна

10. **`.delivery/checks.json` — формат в целевом репозитории** (например, `po-helper-org/poh-demo-checkout`). Описывает service (port, start, health_path) и checks (массив проверок). Отсутствие файла не блокирует релиз, но отражается в плане.

11. **Откат при провале — `workflow.py`, строки 254-271, 453-470**. Метод `_rollback()` делает revert мержа и возврат прежней сборки на прод. Окно наблюдения не меняет логику отката — провал во время окна = обычный откат.

12. **GitHub-порт — `poh_delivery/github.py`**. Реализует работу с GitHub (merge, revert, create_release и т.д.). `revert_merge()` (ищется в файле) используется для отката при провале шага.

# Открытые вопросы

1. **Какие именно метрики контейнера доступны через Docker без дополнительных зависимостей?** `docker inspect` покажет статус, PID, время создания, restart count. Нужно подтвердить, что restart count — то, что нужно для определения "перезапускался ли".

2. **Как настраивается длительность окна?** Через переменную окружения (как `DELIVERY_READY_TIMEOUT` в `prod.py`) или через `.delivery/checks.json`? Требование говорит "настраивается", но не уточняет источник конфигурации.

3. **Что значит "жив ли сервис всё окно"?** Периодическая проверка health_path (как в `_wait_ready`) или однократная проверка в конце окна? Требование говорит "наблюдать", что подразумевает периодичность, но не даёт конкретики.

4. **Какое разумное умолчание и потолок длительности?** Требование упоминает разумное умолчание и потолок, но не даёт значений. Нужно решить на основе практики.

5. **Какие проверки считать "финальными"?** Те же, что из `.delivery/checks.json`, или отдельный набор? Требование говорит "финальный прогон проверок в конце окна обязателен", но не уточняет, это те же проверки или другие.

6. **Как хранить информацию о наблюдении в StepOutcome?** Новые поля (observation_duration, observation_result, observation_detail) или существующие?

# Диалог

## Ход 1: Обзор репозитория

**Инструмент:** `get_overview` → `list_repos` → `get_overview`

**Ответ:** Обнаружил workspace с одним репозиторием `poh-demo-checkout`. Это не тот репозиторий, где реализована система выкатки — это демо-сервис для проверки контура. Поискал по "release deploy checkout step verification" — результатов нет.

## Ход 2: Переключение на правильный репозиторий

**Инструмент:** `ls -la`, `cat README.md`

**Ответ:** Рабочая директория содержит репозиторий `poh-delivery-agent-8`. README объясняет: это релиз-менеджер контура производства, отдельный репозиторий. Ключевые файлы: `workflow.py` (семь шагов релиза), `prod.py` (выкатка контейнером + проверки), `activities.py` (активности Temporal).

## Ход 3: Изучение prod.py

**Инструмент:** `Read poh_delivery/prod.py`

**Ответ:** `DockerProd` реализует выкатку на прод-контур как контейнер на том же хосте. Критичные методы:
- `deploy()` — поднятие контейнера из состояния репозитория на нужном SHA
- `verify()` — прогон проверок из `.delivery/checks.json`
- `_wait_ready()` — ожидание, пока сервис ответит на health_path
- `current_sha()` — текущий SHA на проде

## Ход 4: Изучение workflow.py

**Инструмент:** `Read poh_delivery/workflow.py`

**Ответ:** Критичное место — строки 213-252, где происходит:
- `delivery_merge` — влитие PR
- `delivery_read_checks` — перечитывание проверок из влитого состояния
- `delivery_deploy` — выкатка на прод-контур
- `delivery_verify` — проверки живого сервиса
- Решение: если `deployed.ok and not failed` → шаг успешен, иначе откат (строки 254-271).

Именно между `deploy` и `verify` нужно внедрить окно наблюдения, а `verify` сделать финальной проверкой после окна.

## Ход 5: Изучение activities.py и ports.py

**Инструмент:** `Read poh_delivery/activities.py`, `Read poh_delivery/ports.py`

**Ответ:** Активности — все внешние эффекты как отдельные функции. Новая активность для окна наблюдения добавляется сюда. `ProdPort` — протокол с методами `deploy()`, `verify()`, `current_sha()`. Новые методы для наблюдения добавляются в протокол.

## Ход 6: Изучение model.py и render.py

**Инструмент:** `Read poh_delivery/model.py`, `Read poh_delivery/render.py`

**Ответ:** `StepOutcome` хранит результат шага (ok, checks, rolled_back). Новые поля для наблюдения добавляются сюда. `render.report_md()` формирует отчёт релиза с разделом "Проверки по шагам" — туда добавляется информация о наблюдении.

## Ход 7: Изучение тестов

**Инструмент:** `Read tests/test_workflow.py`, `Read tests/test_prod.py`, `grep -n "deploy\|verify" tests/test_workflow.py`

**Ответ:** Тесты проверяют порядок внешних эффектов. Критичная проверка — строка 209: порядок `["merge:5", "checks:merged5", "deploy:merged5", "verify"]`. После внедрения окна наблюдения порядок изменится. `test_prod.py` проверяет разбор `.delivery/checks.json` и вердикты по ответу сервиса.


---
session: rw-analysis-po-helper-org__poh-delivery-agent-8
workspace: product
turns: 6
started: 2026-08-25T04:22:12Z
finished: 2026-08-25T04:22:24Z
---

# Свежесть индекса

| репозиторий | SHA | возраст индекса |
|---|---|---|
| `poh-bft-writer` | `c7fe9bd7d1b2` | 5 мин |
| `poh-cortex` | `3c3e1a3a9e83` | 5 мин |
| `poh-helper` | `18512c1e7ce9` | 5 мин |
| `poh-infra` | `3022ba18116d` | 6 мин |
| `poh-issue-agents` | `c0534f47500c` | 6 мин |
| `poh-memory-engine` | `194e39319b3f` | 5 мин |
| `poh-pr-agents` | `9f15ada9e4e9` | 6 мин |
| `poh-scheduller` | `04cc3f47aff9` | 5 мин |
| `poh-sprint-agents` | `c41cb5b2ce18` | 5 мин |
| `poh-demo-checkout` | `2e7c62aa955e` | 4 сут |

# Диалог

## Ход 1 · `get_overview` · product

**Запрос:**

```json
{
  "repo": "all"
}
```

**Ответ:**

{
  "workspace": true,
  "workspace_root": "",
  "total_repos": 1,
  "total_files": 15,
  "total_symbols": 21,
  "repos": [
    {
      "alias": "default",
      "path": "/workspaces/product/poh-demo-checkout",
      "summary": "Repository Overview: poh-demo-checkout",
      "file_count": 15,
      "symbol_count": 21,
      "is_default": false
    }
  ],
  "hint": "Use repo='<alias>' to query a specific repo. Omit repo to use the default."
}

## Ход 2 · `get_overview` · product

**Запрос:**

```json
{
  "repo": "default",
  "include": [
    "content",
    "outline"
  ]
}
```

**Ответ:**

{
  "error": "Repository not found: default",
  "remedy": "Call list_repos to see the valid repo aliases, or omit the 'repo' argument to use the default repository."
}

## Ход 3 · `list_repos` · product

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
    "semantic_search": false
  }
}

## Ход 4 · `get_overview` · product

**Запрос:**

```json
{
  "include": [
    "content",
    "outline"
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
        "descendants": 3,
        "children": [
          {
            "section": "2.1",
            "page_id": "file_page:src/pricing.mjs",
            "title": "File: src/pricing.mjs",
            "page_type": "file_page",
            "target_path": "src/pricing.mjs",
            "descendants": 1
          },
          {
            "section": "2.2",
            "page_id": "file_page:src/server.mjs",
            "title": "File: src/server.mjs",
            "page_type": "file_page",
            "target_path": "src/server.mjs"
          }
        ]
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
    "index_age_days": 4,
    "indexed_commit": "2e7c62aa955e",
    "index_behind": false,
    "embedder": "mock",
    "embedder_degraded": false,
    "semantic_search": false
  }
}

## Ход 5 · `search_codebase` · product

**Запрос:**

```json
{
  "query": "release deploy checkout step verification",
  "limit": 10
}
```

**Ответ:**

{
  "results": [
    {
      "title": "Repository Overview: poh-demo-checkout",
      "page_type": "repo_overview",
      "snippet": "# Repository Overview: poh-demo-checkout\n\n**Files:** 12 | **Lines:** 1446\n\n## Project Summary\n\n\n`poh-demo-checkout` is a markdown codebase of 12 files. Execution starts at `src/server.mjs`.\n\n\n\n\n## Ent",
      "relevance_score": 3.0,
      "sources": [
        "fts"
      ],
      "target_path": "poh-demo-checkout",
      "confidence_score": 1.0
    }
  ],
  "_meta": {
    "index_age_days": 4,
    "indexed_commit": "2e7c62aa955e",
    "index_behind": false,
    "embedder": "mock",
    "embedder_degraded": false,
    "semantic_search": false
  }
}

## Ход 6 · `get_context` · product

**Запрос:**

```json
{
  "targets": [
    "README.md",
    "CLAUDE.md",
    "DEMO.md",
    ".github/workflows/ci.yml",
    ".github/workflows/pr-review.yml"
  ],
  "include": [
    "skeleton"
  ]
}
```

**Ответ:**

{
  "targets": {
    "README.md": {
      "target": "README.md",
      "type": "file",
      "docs": {
        "summary": "README.md: 68-line document, 5 headings, no indexed symbols.",
        "file_preview": {
          "lines": 68,
          "chars": 2808,
          "headings": [
            "# poh-demo-checkout",
            "## Сервис",
            "# {\"goods\":2000,\"delivery\":300,\"total\":2300}",
            "## Проверки",
            "## Автоматика"
          ],
          "note": "This file has no indexed symbols, so there is no structural card for it. The fields above are counts and verbatim excerpts. Read the file for its full content."
        }
      },
      "hotspot": false,
      "freshness": {
        "confidence_score": null,
        "freshness_status": null,
        "is_stale": null
      },
      "architectural_layer": {
        "name": "Application",
        "description": "",
        "role": "entry_point"
      },
      "skeleton": {
        "mode": "raw",
        "tokens": 702,
        "full_tokens": 702,
        "pct_of_full": 100.0,
        "bodies_kept": [],
        "text": "# poh-demo-checkout\n\nМаленький сервис расчёта стоимости заказа. Существует не ради себя: это\n**демо-стенд контура производства** — на нём гоняется полный цикл от заявки до\nвлитого PR, и код намеренно простой, чтобы предметом разбора был контур, а не\nпредметная область.\n\n```\nIssue заведён\n  └─ Issue-Agent: триаж (advisor:* + priority:* + phase:classified)\n       └─ аналитика FNR → ветка research/issue-N + системные требования\n            ├─ остались вопросы [УТОЧНИТЬ] → комментарий и ожидание ответа\n            └─ декомпозиция → SubIssue по релизам MVP / GROW / SUPPORT\n                 └─ ready-for-dev + чеклист готовности\n                      └─ OpenHands: разработка → PR с Closes #N\n                           ├─ edge-кейсы по дороге → .followups.md → SubIssue\n                           └─ PR-Agent: ревью\n                                └─ круг правок → /review → «замечаний нет»\n```\n\nПодзадачи декомпозиции своей разработки НЕ заводят: весь объём MVP исполняет\nродитель одним прогоном и одним PR. Иначе на репозитории одновременно работали бы\nнесколько агентов.\n\n## Сервис\n\n`POST /quote` считает заказ: сумму позиций, доставку и итог.\n\n```bash\nnpm start\ncurl -sX POST localhost:8080/quote \\\n  -H 'content-type: application/json' \\\n  -d '{\"items\":[{\"sku\":\"a\",\"price\":1000,\"qty\":2}]}'\n# {\"goods\":2000,\"delivery\":300,\"total\":2300}\n```\n\nДоставка бесплатна от 3000 (включительно). Зависимостей у сервиса нет — это его\nсвойство, а не упущение.\n\n## Проверки\n\n```bash\nnode --test \"tests/*.test.mjs\"\n```\n\n## Автоматика\n\n| Workflow | Когда | Что делает |\n|---|---|---|\n| `ci.yml` | PR и push в main | тесты |\n| `openhands-resolver.yml` | диспатч от Issue-Agent (либо метка `fix-me`) | разработка по Issue → PR |\n| `pr-review.yml` | три входа: вызов из разработки, событие `pull_request`, команда `/review` в PR | ревью PR-Agent + доклад в цикл |\n\nТри входа у ревью не для надёжности, а по необходимости. PR, созданный\n`GITHUB_TOKEN`, событий workflow не порождает — оттуда вызов из разработки. PR,\nоткрытый контуром на своём сервере (режим `local`), Actions вообще не проходит —\nоттуда `pull_request`. Перепроверку после круга правок просит сам контур командой\n`/review`, и пишет он её токеном GitHub App, то есть ботом: фильтра по типу автора\nна этой команде нет намеренно.\n\n`pr-review.yml` определяет исход **по выводу** `pr-agent`, а не по коду возврата:\nна rate-limit модели тот пишет `Failed to review PR` в лог, оставляет в PR\nзаглушку «Preparing review...» и завершается успешно. Доклад в цикл несёт\n`headRefOid` — коммит, по которому шло ревью: без него круги правок выглядели\nповторной доставкой одного и того же факта, и второй круг не начинался.\n\nПравила, по которым работают агенты, — в [`AGENTS.md`](AGENTS.md) и\n[`CLAUDE.md`](CLAUDE.md). Конфигурация контура целиком — `poh-infra/harness`.\n",
        "verified": true,
        "note": "No usable symbol bounds for this file — returned source as-is."
      }
    },
    "CLAUDE.md": {
      "target": "CLAUDE.md",
      "type": "file",
      "docs": {
        "summary": "CLAUDE.md: 67-line document, 6 headings, no indexed symbols.",
        "file_preview": {
          "lines": 67,
          "chars": 2707,
          "headings": [
            "# CLAUDE.md",
            "## Git-workflow (обязательно)",
            "## MVP первым, edge-кейсы — в SubIssue (обязательно)",
            "## Метки контура",
            "## Проверки",
            "## Границы кода"
          ],
          "note": "This file has no indexed symbols, so there is no structural card for it. The fields above are counts and verbatim excerpts. Read the file for its full content."
        }
      },
      "hotspot": false,
      "freshness": {
        "confidence_score": null,
        "freshness_status": null,
        "is_stale": null
      },
      "architectural_layer": {
        "name": "Application",
        "description": "",
        "role": "entry_point"
      },
      "skeleton": {
        "mode": "raw",
        "tokens": 676,
        "full_tokens": 676,
        "pct_of_full": 100.0,
        "bodies_kept": [],
        "text": "# CLAUDE.md\n\nГайд для Claude Code в этом репозитории. Он же демо-стенд контура производства —\nздесь ходят Issue-Agent, OpenHands и PR-Agent, поэтому конвенции жёстче обычного:\nна них держится автоматика.\n\n## Git-workflow (обязательно)\n\n**Правило:** любая доработка ведётся через Issue. Цель — прозрачная хронология:\nлюбой агент восстанавливает контекст, читая один Issue, без раскопок в чате.\n\n1. **Issue первым делом.** Запрос на доработку → перед любым кодом заводится\n   Issue. Без Issue работа не начинается.\n2. **HowToDemo в Issue.** Конкретный сценарий, которым проверяется готовность:\n   что запустить, что сделать, что должно получиться. Это граница MVP. Нет\n   HowToDemo — нет критерия «готово».\n3. **Ветка = Issue.** `feature/<номер>-<slug>` либо `fix/<номер>-<slug>`.\n4. **PR = Issue.** `Closes #<номер>` в теле.\n5. **Хронология непрерывна.** Значимый шаг — комментарий в Issue.\n\n## MVP первым, edge-кейсы — в SubIssue (обязательно)\n\nНайденный по дороге edge-кейс **не берётся в работу сразу** — он выносится\nSubIssue с `root-issue: #N` и меткой `origin:agent`. К нему возвращаются после\nтого, как MVP прошёл HowToDemo.\n\nАгент разработки заводит их не сам: он пишет находки в `.followups.md` в корне\nрабочего каталога (раздел `## <кратко>` на находку), а SubIssue по ним создаёт\nконтур своим токеном. Токена у агента нет намеренно — он исполняет код\nрепозитория.\n\nЕдинственное исключение — edge-кейс, без которого HowToDemo не проходит. Тогда\nэто часть MVP, и надо явно сказать, чем именно он ломает демо.\n\n**DON'T:** чинить edge-кейс «раз уж он рядом»; расширять scope ветки находками,\nне блокирующими HowToDemo; держать находки только в чате — не зафиксированные\nSubIssue теряются.\n\n## Метки контура\n\nИх пишет автоматика, и правило простое: **одна метка — один писатель**.\n\n| Метка | Пишет | Смысл |\n|---|---|---|\n| `advisor:*`, `priority:*`, `phase:*` | Issue-Agent | классификация, приоритет, фаза задачи |\n| `research-me`, `build-me` | человек | запуск дорогой стадии |\n| `ready-for-dev` | Issue-Agent | задача подготовлена, можно брать в работу |\n| `origin:agent` | любой агент | артефакт создан агентом, а не человеком |\n| `agents:off` | человек | рубильник: агенты не трогают этот Issue |\n\nЧужую метку агент только читает.\n\n## Проверки\n\n```bash\nnode --test \"tests/*.test.mjs\"\n```\n\nНовая логика — новый тест. Расчёт здесь единственное, что может сломаться молча.\n\n## Границы кода\n\n- Арифметика — в `src/pricing.mjs`, чистыми функциями. `src/server.mjs` только\n  разбирает запрос и отдаёт коды: логика в обёртке — логика, которую тест не\n  увидит.\n- Зависимостей у сервиса нет. Новая заводится, только если задачу нечем закрыть\n  из стандартной библиотеки, и причина пишется рядом с кодом.\n",
        "verified": true,
        "note": "No usable symbol bounds for this file — returned source as-is."
      }
    },
    "DEMO.md": {
      "target": "DEMO.md",
      "type": "file",
      "docs": {
        "summary": "DEMO.md: 108-line document, 10 headings, no indexed symbols.",
        "file_preview": {
          "lines": 108,
          "chars": 4935,
          "headings": [
            "# Демо контура производства",
            "## Прогон, на котором это проверено",
            "## Две активности контура",
            "## Сценарий",
            "### Акт 1. Заявка становится задачей",
            "### Акт 2. Research",
            "### Акт 3. Develop",
            "### Акт 4. Ревью",
            "## Что показать в Temporal",
            "## Границы, о которых честно сказать"
          ],
          "note": "This file has no indexed symbols, so there is no structural card for it. The fields above are counts and verbatim excerpts. Read the file for its full content."
        }
      },
      "hotspot": false,
      "freshness": {
        "confidence_score": null,
        "freshness_status": null,
        "is_stale": null
      },
      "architectural_layer": {
        "name": "Application",
        "description": "",
        "role": "entry_point"
      },
      "skeleton": {
        "mode": "raw",
        "tokens": 1233,
        "full_tokens": 1233,
        "pct_of_full": 100.0,
        "bodies_kept": [],
        "text": "[repowise#cc2a1658d933: 109 lines omitted (~1233 tokens); restore: repowise expand cc2a1658d933]",
        "verified": true,
        "note": "No usable symbol bounds for this file — returned source as-is.",
        "omitted": true
      }
    },
    ".github/workflows/ci.yml": {
      "target": ".github/workflows/ci.yml",
      "type": "file",
      "docs": {
        "summary": "ci.yml: 28 lines, no indexed symbols.",
        "file_preview": {
          "lines": 28,
          "chars": 605,
          "head": [
            "# Проверка каждого PR и каждого коммита в main.",
            "#",
            "# Зависимостей у сервиса нет намеренно — это его свойство, а не упущение, и",
            "# `npm ci` здесь молча создал бы их появлению повод.",
            "name: CI",
            "on:",
            "  pull_request:",
            "  push:",
            "    branches: [main]",
            "concurrency:",
            "  group: ci-${{ github.ref }}",
            "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            "permissions:",
            "  contents: read",
            "jobs:"
          ],
          "note": "This file has no indexed symbols, so there is no structural card for it. The fields above are counts and verbatim excerpts. Read the file for its full content."
        }
      },
      "hotspot": false,
      "freshness": {
        "confidence_score": null,
        "freshness_status": null,
        "is_stale": null
      },
      "architectural_layer": {
        "name": "Config",
        "description": "",
        "role": "entry_point"
      },
      "skeleton": {
        "mode": "raw",
        "tokens": 151,
        "full_tokens": 151,
        "pct_of_full": 100.0,
        "bodies_kept": [],
        "text": "# Проверка каждого PR и каждого коммита в main.\n#\n# Зависимостей у сервиса нет намеренно — это его свойство, а не упущение, и\n# `npm ci` здесь молча создал бы их появлению повод.\nname: CI\n\non:\n  pull_request:\n  push:\n    branches: [main]\n\nconcurrency:\n  group: ci-${{ github.ref }}\n  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n\npermissions:\n  contents: read\n\njobs:\n  tests:\n    name: тесты\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-node@v4\n        with:\n          node-version: '22'\n      - run: node --test \"tests/*.test.mjs\"\n",
        "verified": true,
        "note": "No usable symbol bounds for this file — returned source as-is."
      }
    },
    ".github/workflows/pr-review.yml": {
      "target": ".github/workflows/pr-review.yml",
      "type": "file",
      "docs": {
        "summary": "pr-review.yml: 192 lines, no indexed symbols.",
        "file_preview": {
          "lines": 192,
          "chars": 8863,
          "head": [
            "# Ревью PR (PR-Agent на GLM) + доклад в цикл Issue.",
            "#",
            "# Два входа, один код ревью:",
            "#",
            "#   workflow_call   — вызов из прогона разработки сразу после открытия PR.",
            "#                     Именно вызов, а не отдельный триггер: PR, созданный",
            "#                     GITHUB_TOKEN, НЕ порождает событий workflow (правило",
            "#                     GitHub против рекурсии), и ревью по событию `pull_request`",
            "#                     на таком PR не запустилось бы никогда. Вызванный workflow",
            "#                     идёт частью того же прогона и этого ограничения не знает.",
            "#",
            "#   issue_comment   — повторная проверка после правок: человек (или Claude Code)",
            "#                     пишет `/review` в PR. Комментарий от живого пользователя",
            "#                     события порождает, здесь ограничения нет.",
            "#"
          ],
          "note": "This file has no indexed symbols, so there is no structural card for it. The fields above are counts and verbatim excerpts. Read the file for its full content."
        }
      },
      "hotspot": false,
      "freshness": {
        "confidence_score": null,
        "freshness_status": null,
        "is_stale": null
      },
      "architectural_layer": {
        "name": "Config",
        "description": "",
        "role": "entry_point"
      },
      "skeleton": {
        "mode": "raw",
        "tokens": 2215,
        "full_tokens": 2215,
        "pct_of_full": 100.0,
        "bodies_kept": [],
        "text": "[repowise#e0949a9c1e12: 193 lines omitted (~2215 tokens); restore: repowise expand e0949a9c1e12]",
        "verified": true,
        "note": "No usable symbol bounds for this file — returned source as-is.",
        "omitted": true
      }
    }
  },
  "_meta": {
    "timing_ms": 117.4,
    "index_age_days": 4,
    "indexed_commit": "2e7c62aa955e",
    "index_behind": false,
    "embedder": "mock",
    "embedder_degraded": false,
    "semantic_search": false,
    "omitted": {
      "refs": [
        "e0949a9c1e12",
        "cc2a1658d933"
      ],
      "tokens": 3448,
      "restore": "Run `repowise expand <ref>` from the repo, or call get_symbol(\"repowise#<ref>\", query=...) to retrieve the omitted content."
    }
  },
  "truncated": true,
  "dropped_targets": [],
  "dropped_symbols": {}
}
