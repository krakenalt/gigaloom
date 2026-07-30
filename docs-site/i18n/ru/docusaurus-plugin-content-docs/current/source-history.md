# История исходников и миграция

GigaLoom извлечён с отфильтрованной историей из общего репозитория
[`ai-forever/gpt2giga`](https://github.com/ai-forever/gpt2giga). Это
**историческая ссылка на исходники**. Текущая разработка, документация, issues
и releases находятся в
[`krakenalt/gigaloom`](https://github.com/krakenalt/gigaloom).

Первый target-owned дистрибутив называется `gigaloom`. Исторические releases
`gpt2giga-harness` остаются доступны, но не получают новых target releases.
Standalone-дистрибутив использует только Python namespace `gigaloom` и команду
`giga`; compatibility namespace и command shim не поставляются. Существующие
пути локального состояния обрабатываются отдельным этапом миграции.

Старые compare links в changelog намеренно ведут в исторический репозиторий,
чтобы pre-split tags оставались доступны. Они не означают текущего владения или
зависимости от source checkout.

Для миграции удалите старый `gpt2giga-harness`, установите standalone
`gigaloom` и сохраните backup `~/.gpt2giga/harness`. См.
[справочник Harness](harness.md), раздел «Миграция со старого combined
prerelease».
