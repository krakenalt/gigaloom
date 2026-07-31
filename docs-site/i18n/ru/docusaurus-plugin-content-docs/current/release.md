# Релиз

Один release GigaLoom связывает Python, npm, Git и embedded Web assets через
`release/release.json`. Для alpha Native Agent Gateway точная identity такова:

| Поверхность | Identity |
|---|---|
| Canonical release | `0.7.0-alpha.1` |
| Git tag | `v0.7.0-alpha.1` |
| PyPI | `gigaloom==0.7.0a1` |
| npm | `@gigaloom/web@0.7.0-alpha.1` |

Python и npm используют собственный prerelease syntax, но представляют один
release. Root Python metadata и `web/package.json` должны совпадать с manifest.

## Политика тегов

Новые релизы используют только стандартный тег `v<release>`. Создавайте
protected tag на точном проверенном commit из `main` только после build, parity,
checksum, denylist и attestation gates immutable candidate. Publish workflow
требует, чтобы tag указывал на candidate SHA; workflow не создаёт, не двигает и
не исправляет теги.

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
4. Запустите build-only candidate workflow с текущего tip `main`.
5. Проверьте wheel, sdist, npm tarball, metadata, общий Web content digest,
   checksums, SBOM, licenses, isolated installs и attestation.
6. Убедитесь, что release commit находится в `main`, а документированные
   main/tag rulesets активны.
7. Убедитесь, что Trusted Publishers PyPI и npm указывают точные
   project/package, repository, publish workflow и environment
   `release-production`, а required reviewers готовы.
8. Отдельно зафиксируйте candidate run ID, полный source SHA и SHA-256 файла
   `candidate-manifest.json`.
9. Создайте protected standard tag на этом source SHA.
10. Запустите protected publish workflow с записанной candidate identity и
    правильным recovery mode.

## Двухфазный workflow

`.github/workflows/publish-pypi.yml` запускается вручную, строит и аттестует
один retained candidate, но не публикует registry packages или GitHub Release.

`.github/workflows/release-publish.yml` — отдельный manual workflow под защитой
environment `release-production`. Он скачивает retained candidate по run ID и
полному SHA, проверяет переданный оператором manifest digest, tag, ancestry,
metadata, checksums, byte parity и legacy denylist и ничего не пересобирает.
Затем он проверяет public registry state до запроса OIDC credentials.

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
