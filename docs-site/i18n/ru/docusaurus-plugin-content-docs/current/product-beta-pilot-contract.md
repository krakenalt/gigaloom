# Контракт пилота Product Beta

**Дата вступления в силу:** 2026-08-04

**Область:** локальные данные design-partner пилота GigaLoom 0.9

## Явное согласие и граница приватности

Участие и каждый экспорт отчёта требуют явного действия пользователя.
GigaLoom не планирует, не загружает, не передаёт и не рассылает отчёт. В отчёт
входят агрегированные статусы, количества, длительности, доли в базисных
пунктах, коды причин и факты усечения источников. В него не входят prompts,
responses, содержимое сообщений и событий, credentials, данные аккаунтов,
неограниченные пути репозитория и намерения пользователя, выведенные из
filesystem timestamps.

Участник сам выбирает выходной файл и контролирует любое последующее
распространение. Поддержка не требует native-agent homes, сырые transcripts,
captured traffic или credentials как условие участия.

## Cohort и определения метрик

Pilot cohort — явно согласованный набор проектов на закреплённом candidate
GigaLoom 0.9. Отчёт ограничен одним catalog project и периодом не более 366
дней. Activation начинается с сохранённого времени создания catalog project,
а не с install time или filesystem proxy. Success, approvals, recovery, review,
использование Work/Inbox/Automations/Library, Thread Relay и gateway вычисляются
только из существующих durable owners. Отсутствующие или недоступные факты
остаются `unknown`; bounded scans явно сообщают об усечении.

## Критерии остановки

Остановить сбор и не запрашивать отчёт необходимо, если выполняется любое
условие:

- участник отозвал согласие;
- появились content, credentials, private paths или provider-hidden state;
- генерация требует telemetry, network exporter, private-home scraping или
  запуска dependency;
- метрика не отличает сохранённое evidence от предположения;
- нельзя доказать project/actor scope, redaction или boundedness;
- candidate вызывает потерю данных, обход authority или silent route fallback.

## Контракт решения

Допустимы только решения `continue`, `narrow`, `internal_only`, `defer` или
`stop`. Решение фиксирует schema/version отчёта, границы cohort, unknown и
truncated metrics и причину. Evidence не разрешает outreach, data access,
publication, telemetry, provider traffic или product rollout; для каждого
действия требуется отдельное явное разрешение.
