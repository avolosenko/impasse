# Этап 1 — Инфраструктура (черновик, ждёт замеров на GPU)

## Сделано

- Репозиторий по 9a: README (англ.), `.gitignore` (`config.local.yaml`, `*.gguf`, `llama.cpp/`, `runs/`, `.servers/`, `bench/test/`), `config.yaml`, `config.local.example.yaml`, `pyproject.toml`. LICENSE (MIT) уже был.
- `harness/config.py` — загрузка `config.yaml` + deep merge `config.local.yaml`; привязка роль → сервер → URL (вторая GPU = правка `servers:`/`roles:`).
- `harness/servers.py` + `tools/servers.sh` — `start | stop | switch | status` для llama-server; состояние и лог в `.servers/`, ожидание `/health`, замер времени старта/переключения, внятная ошибка при падении сервера на старте (хвост лога).
- `harness/llm.py` — клиент OpenAI-совместимого API llama-server: три режима ограничения вывода (`json_schema`, `json_object`, `grammar`/GBNF), `enable_thinking` через `chat_template_kwargs`, учёт токенов и латентности, валидация по pydantic-схеме.
- `tools/smoke.py` — smoke-тест: `/props` (слоты, контекст на слот ≥ 8k), структурированный вывод в каждом режиме на схеме действий 3.3, проверка выключенного thinking, ток/с при 1 и N параллельных запросах, скорость обработки длинного промпта (~6k токенов), VRAM, время переключения, вариант KV-кэша q8_0, попытка смешанного режима (два сервера).
- `tools/estimate_matrix.py` — оценка времени полной матрицы по результатам smoke.
- Тесты harness'а (`tests/`, 25 шт., без GPU; сервер подменяется фейковым процессом). Результат: **25 passed**.

## Окружение (обнаружено)

- RTX 5070, 12227 MiB; **~1.5 GB VRAM занято до запуска чего-либо** (Windows/WSL) — реально доступно ~10.5 GB, это учитывается в запасе ≥ 1 GB.
- CUDA toolkit 13.2 (`/usr/local/cuda`), cmake, gcc есть.
- **llama.cpp** собран с CUDA вне репозитория: commit `e6443bd7eb970d940b0669cd9ea0d82d91d620a1`, `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120` (cmake превращает в `120a`), Release, GCC 15.2, CUDA 13.2.86. Собраны цели `llama-server` и `llama-cli`. `llama-server --list-devices`: `CUDA0: RTX 5070 (12199 MiB, 11018 MiB free)`.
  - Для сборки понадобился пакет `cuda-nvml-dev-13-2`: текущий llama.cpp всегда подключает `nvml.h`, а в урезанной установке CUDA его нет.
  - Флаги сборки подтверждены: `-fa on|off|auto`, `--jinja` (включён по умолчанию), `-ctk/-ctv`, `-np/--parallel`. Есть также серверный `--reasoning on|off|auto`; переключать thinking будем запросом через `chat_template_kwargs`, `--reasoning` держим как запасной вариант, если шаблон модели проигнорирует параметр.
- **GGUF-моделей нет** — ждём выбора конкретных файлов.
- Системный Python 3.14; для проекта создан `.venv` на Python 3.12 (зависимости: httpx, pydantic, pyyaml, pytest).

## Не сделано (нужны llama.cpp и модели)

- Реальные замеры: VRAM, ток/с, итоговые `-c`/`--parallel`, рабочий режим структурированного вывода, влияние `-ctk/-ctv q8_0`, время переключения моделей.
- Смешанный режим (9B + 4B на одной GPU).
- Оценка времени полной матрицы и, если > 48 ч, предложение по сокращению.

## Решения, которые стоит подтвердить

- `-fa on` (новый синтаксис llama.cpp; в старых сборках был флаг `-fa` без значения) и `--jinja` (нужен для `chat_template_kwargs`/thinking у Qwen) добавлены в аргументы по умолчанию.
- Стартовые параметры смешанного режима: 9B `-c 8192 --parallel 1`, 4B `-c 16384 --parallel 2`.
