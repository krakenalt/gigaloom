# Среды агентов разработки

Страница **Coding Agents → Agent runtimes** объединяет три связанные, но
разные сущности:

| Источник | Что это | Где отображается |
|---|---|---|
| Встроенный профиль | Проверенное описание уже установленного provider CLI: Codex, Claude, Gemini или Pi | `giga agent list` |
| ACP Registry | Запись официального удалённого каталога, которую GigaLoom устанавливает в приватный managed root | Вкладки **ACP Registry** и **Installed** |
| Local manifest | Advanced TOML-описание локального executable или structured route, которым вы управляете самостоятельно | Вкладка **Local manifests** |

Установка из Registry не изменяет глобальные npm, Python и `PATH`. Регистрация
local manifest не устанавливает и не запускает указанную программу.

## Как исправить `503 Agent inventory unavailable` при первом запуске

Inventory endpoint читает только ранее проверенный локальный snapshot Registry
и не выполняет скрытых сетевых запросов. В новом data directory snapshot ещё
нет, поэтому запрос может вернуть `503`:

```text
GET /api/agent-runtimes/inventory
```

Для первого запуска выполните следующие шаги:

1. Откройте **Coding Agents → Agent runtimes**.
2. Один раз нажмите **Refresh registry**. Эта явная операция скачивает и
   проверяет metadata официального ACP Registry, но не устанавливает и не
   запускает agent.
3. После успешного refresh откройте вкладку **ACP Registry**.

Эквивалентная команда CLI:

```bash
giga agent search "" --refresh --json
```

Проверенный snapshot сохраняется в
`GIGALOOM_DATA_DIR/agent_profiles/acp_registry/`. По умолчанию data directory —
`~/.gigaloom`. UI server и CLI должны использовать один и тот же
`GIGALOOM_DATA_DIR`.

Если refresh по-прежнему завершается ошибкой, запустите CLI-команду в том же
окружении, что и UI server, и посмотрите последнюю ошибку. Обычные причины:

- registry host недоступен из-за DNS, TLS, proxy или firewall;
- ответ не соответствует поддерживаемому ограниченному Registry v1 JSON;
- local cache pointer или snapshot не проходит integrity validation;
- UI server и CLI используют разные data directories.

При ошибке целостности cache остановите все процессы GigaLoom, использующие
этот data directory, переместите `agent_profiles/acp_registry` в backup,
перезапустите UI и явно выполните refresh. Не редактируйте `current.json` и
snapshot-файлы вручную. Устаревший, но валидный cached snapshot остаётся
доступным, если последующий сетевой refresh завершился ошибкой.

## Почему подтверждённая установка может быть отклонена

Registry refresh и install preview отделены от установки artifact. Ответы `200`
для inventory, refresh, preview и чтения operation подтверждают исправность
каталога, но не дают полномочий на скачивание и запуск agent.

Текущая сборка автоматически подключает системный network-deny launcher для
initialize-only compatibility probe:

- macOS: `/usr/bin/sandbox-exec` с явным профилем `deny network*`;
- Linux: `/usr/bin/bwrap` или `/bin/bwrap` с отдельным network namespace.

Подтверждённая server-owned транзакция скачивает точный проверенный artifact,
сверяет заявленный digest, распаковывает его в приватный managed root и перед
активацией запускает probe через этот launcher. Если поддерживаемого launcher
нет, установка fail-closed завершается до создания operation с кодом:

```text
managed_agent_network_isolation_required
```

Windows и Linux без Bubblewrap пока не поддерживают managed installation; на
таких host установите provider CLI отдельно и используйте проверенный local
manifest. Если macOS сообщает `sandbox_apply: Operation not permitted`, запускайте
`giga ui` из обычного terminal, а не из другой restrictive sandbox. Не обходите
проверку изменением внутреннего admission flag.

При запуске старой сборки всегда задавайте абсолютный data directory:

```bash
export GIGALOOM_DATA_DIR="$HOME/.gigaloom"
giga ui
```

Текущая сборка раскрывает `~` до создания registry и operation state. Если
старая сборка уже создала буквальный `./~/.gigaloom`, остановите GigaLoom и
сделайте backup этого каталога перед согласованием с `$HOME/.gigaloom`; не
запускайте два server с двумя разными каталогами.

## Установка agent из ACP Registry через UI

1. Обновите Registry и откройте вкладку **ACP Registry**.
2. Отфильтруйте записи по platform, distribution, integrity или license.
3. Нажмите **Review install** у нужной записи.
4. Проверьте точную version, source, managed path, integrity policy и side
   effects в плане, сформированном backend.
5. Если предложенный id конфликтует со встроенной командой или другим agent,
   укажите безопасный local alias.
6. Подтвердите проверенный план до истечения его срока действия.
7. Откройте **Installed**, выполните **Probe**, при необходимости пройдите
   provider-owned authentication и нажмите **Use in new run**.

Разрешайте unverified distribution только после проверки source и осознанного
принятия того, что artifact не защищён registry digest. Browser не выбирает
distribution самостоятельно: backend выбирает вариант для текущей platform и
привязывает подтверждение к проверенному плану.

## Установка agent из ACP Registry через CLI

CLI обнаруживает тот же platform isolation launcher, что и UI.

Сначала обновите Registry и получите доступные id:

```bash
giga agent search "" --refresh --json
```

Посмотрите план для точной записи без установки:

```bash
giga agent add <registry-id> --dry-run --json
```

Установите проверенную запись:

```bash
giga agent add <registry-id> --yes --json
```

Если preview сообщает о конфликте identity, задайте явный alias:

```bash
giga agent add <registry-id> --as <local-agent-id> --dry-run --json
giga agent add <registry-id> --as <local-agent-id> --yes --json
```

Используйте `--allow-unverified` только тогда, когда preview показывает
отсутствие проверенного integrity evidence у выбранного distribution и вы
принимаете этот риск.

Проверьте установленный runtime:

```bash
giga agent list --json
giga agent inspect <local-agent-id> --json
giga agent probe <local-agent-id> --json
```

Обновления устанавливаются side-by-side и только явно. Предыдущую сохранённую
revision можно восстановить через `rollback`:

```bash
giga agent outdated --refresh --json
giga agent update <local-agent-id> --yes --json
giga agent rollback <local-agent-id> --yes --json
```

## Регистрация advanced local manifest

Используйте local manifest, если executable уже управляется вне GigaLoom или
нужно описать собственный native/structured route. Manifest — строгий TOML-файл,
который должен оставаться по зарегистрированному пути.

Минимальный native-пример:

```toml
schema_version = 1
agent_id = "my-agent"
display_name = "My Agent"
aliases = []
profile_version = "1.0.0"
auth_owner = "provider"
platform_support = ["darwin", "linux", "win32"]
compatibility_profiles = []
structured_routes = []

[source]
kind = "local_manifest"
origin = "local:my-agent.toml"
revision = "1.0.0"
trust_class = "local"
reviewed = false

[native]
executable_names = ["my-agent"]
provider_home_markers = []
provider_config_markers = []
version_probe = ["--version"]
supports_managed_terminal = true

[[native.interactive_matchers]]
matcher_id = "my-agent.root"
kind = "empty_suffix"
tokens = []
precedence = 10

[[native.interactive_matchers]]
matcher_id = "my-agent.prompt"
kind = "single_positional"
tokens = []
precedence = 20

[[native.metadata_matchers]]
matcher_id = "my-agent.metadata"
kind = "any_option"
tokens = ["--help", "--version"]
precedence = 10

[[native.headless_matchers]]
matcher_id = "my-agent.headless"
kind = "first_token"
tokens = ["exec"]
precedence = 10
```

Сначала проверьте регистрацию без изменения state, затем зарегистрируйте
manifest:

```bash
giga agent add --manifest ./my-agent.toml --dry-run --json
giga agent add --manifest ./my-agent.toml --json
giga agent inspect my-agent --json
giga agent probe my-agent --json
```

Регистрация сохраняет digest-bound reference на manifest. Она не копирует
executable, не устанавливает зависимости, не выдаёт credentials и не делает
локальный source проверенным. Если файл изменился или исчез, профиль получает
статус stale до намеренной замены.

## Воспроизведение managed installs на другой машине

Экспортируйте точные установленные revisions, затем синхронизируйте их на
целевой машине без неявного обновления до более новых записей Registry:

```bash
giga agent lock --output agent-lock.json --json
giga agent sync --lock agent-lock.json --yes --json
```

Храните lock-файл в подходящем project-controlled location. Он содержит
artifact identity и integrity evidence, но не provider credentials.
