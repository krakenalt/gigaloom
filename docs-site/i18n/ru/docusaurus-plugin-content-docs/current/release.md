# Релиз

Один release GigaLoom связывает Python, npm, Git и embedded Web assets через
`release/release.json`. Для первого стабильного Native Agent Gateway точная identity такова:

| Поверхность | Identity |
|---|---|
| Canonical release | `0.7.0` |
| Git tag | `v0.7.0` |
| PyPI | `gigaloom==0.7.0` |
| npm | `@gigaloom/web@0.7.0` |

Root Python metadata и `web/package.json` должны точно совпадать с manifest.

## Политика тегов

Новые релизы используют только стандартный тег `v<release>`. Создавайте
protected tag на точном проверенном commit из `main`. Tag запускает immutable
candidate build; публикация начинается только после успешных build, parity,
checksum, denylist и attestation gates. Workflows не создают, не двигают и не
исправляют теги.

Исторические prefix-shaped tags остаются историей. Не используйте эти prefixes
для новых releases. После принятия версии хотя бы одним registry tag нельзя перемещать или
удалять. Ошибочный неопубликованный tag исправляется по audited repository
policy, а не обходится workflow.

## Checklist maintainer

1. Убедитесь, что назначенные backup owners GitHub и PyPI приняли доступ с 2FA
   согласно
   [governance policy](https://github.com/krakenalt/gigaloom/blob/main/GOVERNANCE.md).
2. Обновите оба changelog и проверьте все identities в
   `release/release.json`, `pyproject.toml` и `web/package.json`.
3. Соберите frontend assets и выполните полный non-live quality gate.
4. Убедитесь, что release commit находится в `main`, а документированные
   main/tag rulesets активны.
5. Убедитесь, что Trusted Publishers PyPI и npm указывают точные
   project/package, repository, publish workflow и environment
   `release-production`, а environment не требует reviewer approval.
6. Создайте protected standard tag на этом source SHA. Tag автоматически
   строит и аттестует candidate, затем после успеха запускает publication из
   того же workflow run.
7. Следите за candidate, registry checks и GitHub Release. Зафиксируйте
   candidate run ID, полный source SHA и SHA-256 файла
   `candidate-manifest.json` для recovery.

## Двухфазный workflow

`.github/workflows/publish-pypi.yml` запускается push защищённого `v*` tag,
строит и аттестует один retained candidate, но не публикует registry packages
или GitHub Release.

`.github/workflows/release-publish.yml` — отдельный workflow под environment
`release-production`. Успешное завершение candidate запускает его через
`workflow_run`; resolver использует точный triggering run ID и требует один
непросроченный SHA-bound artifact. Protected job повторно проверяет tag,
ancestry, metadata, checksums, byte parity и legacy denylist и ничего не
пересобирает. Manual dispatch остаётся только для recovery и требует run ID,
полный SHA, tag, manifest digest и recovery mode.

Выберите ровно один mode:

- `initial`: обе версии отсутствуют; опубликовать npm с provenance, затем PyPI;
- `recover-pypi`: точные npm bytes существуют, PyPI отсутствует; публиковать
  только PyPI;
- `recover-npm`: точные PyPI files существуют, npm отсутствует; публиковать
  только npm;
- `release-assets-only`: оба registry содержат точные candidate bytes; не
  публиковать packages и создать GitHub Release последним.

Любой неожиданный filename или digest, malformed registry response либо outage
останавливает workflow. Registry, где операция уже завершилась, при recovery
повторно не публикуется.

## Rollback и recovery

До принятия версии registry можно откатить release automation или source и
построить новый candidate из нового commit. После успеха npm или PyPI version
и tag неизменяемы. Для завершения отсутствующего registry используйте только
точный retained candidate либо увеличьте все release identities и выпустите
новую версию. Нельзя пересобирать partial release, перезаписывать package files
или перемещать tag.

Trusted Publisher, tags, GitHub releases и package publication — отдельные
внешние мутации. См.
[runbook восстановления](https://github.com/krakenalt/gigaloom/blob/main/.github/RELEASE_RECOVERY.md).
