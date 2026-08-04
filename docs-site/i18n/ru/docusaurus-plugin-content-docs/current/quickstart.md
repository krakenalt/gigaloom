# Быстрый старт

Установите GigaLoom и проверьте локальное окружение:

```sh
giga doctor
giga --version
```

Аутентификация остаётся provider-owned. Войдите через нативный CLI Codex,
Claude или Gemini до запуска через GigaLoom.

## Префикс нативной команды

GigaLoom добавляет один префикс и сохраняет остальную команду:

```sh
giga codex exec --json "кратко опиши репозиторий"
giga claude -p "кратко опиши репозиторий"
giga gemini -p "кратко опиши репозиторий"
```

Help, version, stdin/stdout, JSON/JSONL и exit status остаются нативными. Если
CLI отсутствует или его контракт изменился, dispatch завершается fail-closed
до запуска provider session.

## Браузерный cockpit

```sh
giga ui
```

Откройте `http://127.0.0.1:8091/`. По умолчанию listener доступен только через
loopback. В cockpit:

1. выберите или зарегистрируйте локальный проект;
2. выберите provider adapter;
3. просмотрите execution preview и требуемый authority;
4. подтвердите только точное действие, которое хотите выполнить;
5. изучите события, diff и evidence.

Для provider-native terminal workflow используйте `giga <agent>`, а для
governed browser Workbench — `giga ui`.

## Путь work-first в 0.9

Откройте `/web/work` и следуйте пути
`Project -> Thread -> Run -> Evidence -> Action`. До отправки проверьте
route/model support, workspace, read-only сводку Effective Instructions,
authority mode и blockers. После отправки разберите причинную историю run и
элементы Inbox, требующие действия.

Проверьте ограниченную доставку Thread Relay без изменения цели и provider
call:

```sh
giga session send THREAD_ID --text "review failing tests" --dry-run --json
```

Экспортируйте editor schema или локальный beta report с явным согласием:

```sh
giga schema agent
giga evidence product-beta --project PROJECT_ID --output report.json
```

## Запуск через gpt2giga

Установите optional extra и выберите reviewed route по удобному имени или
immutable id:

```sh
uv tool install 'gigaloom[gpt2giga]==0.9.0'
giga --with gpt2giga --model GigaChat-2-Max codex
giga --route codex-gpt2giga-gigachat-2-max codex --help
```

Route Codex/GigaChat имеет статус technical preview. Unknown, stale, ambiguous
или version-drifted evidence приводит к отказу до gateway/provider traffic и не
переключает запуск на другой route.

## Дальше

- [Справочник Harness](harness.md)
- [Work, потоки и контекст](work-threads-and-context.md)
- [Интеграция с gateway](gateway-integration.md)
- [Agents и multi-agent поведение](agents-and-multi-agent.md)
- [Операции](operations.md)
- [Безопасность](security.md)
