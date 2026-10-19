"""Generate a JSON plan and a reviewable HTML report from an offline snapshot.

Usage: python -m scripts.preview_data_reconciliation SNAPSHOT --date YYYY-MM-DD --output DIR
No database connection or apply mode is provided.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from datetime import date
from pathlib import Path

from app.services.data_reconciliation_preview import build_preview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.snapshot.read_bytes()
    snapshot = json.loads(raw)
    plan = build_preview(snapshot, today=args.date)
    plan['snapshot_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    labels = snapshot.get('chat_labels', {})

    def cell(value):
        if value is None:
            return '—'
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        return html.escape(str(value))

    def table(title, rows, fields):
        chunks = [f'<h2>{html.escape(title)} ({len(rows)})</h2><div class="scroll"><table><thead><tr>']
        chunks += [f'<th>{html.escape(label)}</th>' for key, label in fields]
        chunks.append('</tr></thead><tbody>')
        for row in rows:
            chunks.append('<tr>')
            for key, _ in fields:
                val = row.get(key)
                if key == 'chat_id':
                    val = f'{labels.get(str(val), "Чат")} · {val}'
                chunks.append(f'<td>{cell(val)}</td>')
            chunks.append('</tr>')
        chunks.append('</tbody></table></div>')
        return ''.join(chunks)

    body = ['<h1>Согласованность статистики: предварительный отчёт</h1>',
            f'<p>Снимок: {cell(plan["captured_at"])}. Дата расчёта стриков: {args.date}.</p>',
            '<p><b>Это только план. Старые записи в рабочей базе не изменялись.</b> Первая числовая колонка показывает старый рейтинг по счётчикам; вторая — новый расчёт по событиям своего чата; третья — прогноз после отдельного согласования исправления истории.</p>',
            '<p>Глобальный подсчёт сохраняет правило max по чатам за день. Смена расчёта рейтинга групп не меняет глобальные счётчики. Подробности плана и исходные значения доступны в <a href="plan.json">plan.json</a>.</p>',
            '<p>Предлагается сохранить положительные счётчики без событий: создать детализацию, не выдумывая время. Для нескольких событий старое общее уточнение переносится только в последнее событие. Межчатовые копии предлагаются к удалению только при наличии оригинала и совпадении уточнений; неоднозначная группа записей остаётся целиком.</p>',
            '<p>После удаления подтверждённых копий счётчик станет равен числу оставшихся локальных событий; номера событий упорядочиваются. Исходные события не удаляются. Помеченные восстановления исключаются из времени суток, но продолжают учитываться в количестве и стриках. Неподтверждённые копии и неизвестные даты автоматически не исправляются.</p>',
            '<p>Перед применением потребуется свежая резервная копия и повторная проверка, что перечисленные записи не изменились со времени снимка. План не содержит команды применения.</p>']
    summary = plan['summary']
    body.append(table('Итог плана', [
        dict(action='Добавить события для старых положительных счётчиков', result=f"{summary['backfill_events']} событий в {summary['backfill_rows']} дневных записях"),
        dict(action='Удалить подтверждённые копии', result=f"{summary['deduplicate_events']} событий в {summary['deduplicate_rows']} дневных записях"),
        dict(action='Оставить спорные копии без изменений', result=f"{summary['review_events']} событий в {summary['review_rows']} дневных записях"),
        dict(action='Исключить доказанные восстановления из времени суток', result=f"{summary['time_corrections']} событий; количество и стрики сохраняются"),
        dict(action='Оставить неподтверждённое расхождение дат', result=summary['uncertain_times']),
        dict(action='Глобальное количество', result=f"{summary['global_total_before']} → {summary['global_total_after']}"),
        dict(action='Участников с изменением глобальных итогов или стриков', result=summary['global_users_changed']),
        dict(action='Пар чат—участник с изменением стрика', result=summary['chat_streaks_changed']),
    ], [('action','Что изменится'),('result','Прогноз')]))
    body.append(table('Счётчики и места чатов', plan['chat_changes'], [('chat_id','Чат'),('visible','В рейтинге'),('old_ranking_total','Старый рейтинг'),('code_total','Новый расчёт'),('after_repair_total','После исправления истории'),('old_rank','Место было'),('code_rank','Место по новому коду'),('after_repair_rank','Место после исправления')]))
    common=[('chat_id','Чат'),('date','День'),('user','Участник'),('session_id','Сессия')]
    body.append(table('Добавить детализацию старых счётчиков',plan['backfill'],common+[('count','Добавить событий'),('events','Новые события')]))
    body.append(table('Убрать только подтверждённые межчатовые копии',plan['deduplicate'],common+[('copies','Копия → исходное событие'),('before','Состояние до'),('after','Состояние после'),('renumber','Перенумерация оставшихся событий')]))
    body.append(table('Сохранить до ручного разбора',plan['review'],common+[('reason','Причина'),('events','События')]))
    body.append(table('Отметить старые восстановления без точного времени',plan['time_corrections'],common+[('event_id','Событие'),('created_at','Когда записано'),('after_source','Источник после')]))
    body.append(table('Несовпадение даты без доказанного восстановления — оставить',plan['uncertain_times'],common+[('event_id','Событие'),('created_at','Когда записано')]))
    body.append(table('Изменение глобальных итогов/стриков после исправления истории',plan['global_changes'],[('user_id','ID'),('user','Участник'),('before','До'),('after','После')]))
    body.append(table('Изменение чатовых стриков после исправления истории',plan['chat_streak_changes'],[('chat_id','Чат'),('user_id','Участник ID'),('before','До'),('after','После')]))
    output='<!doctype html><html lang="ru"><meta charset="utf-8"><title>Предварительный отчёт Poopbot</title><style>body{font:15px/1.5 system-ui;margin:32px;color:#17212b}h1{font-size:28px}h2{margin-top:36px}.scroll{overflow:auto}table{border-collapse:collapse;min-width:800px;width:100%}td,th{border:1px solid #d9e2eb;padding:9px;text-align:left;vertical-align:top}th{background:#eaf1f7}tr:nth-child(even){background:#f7f9fb}pre{white-space:pre-wrap;background:#eef3f7;padding:16px}p{max-width:1100px}</style>'+''.join(body)+'</html>'
    (args.output / 'report.html').write_text(output,encoding='utf-8')
    print(json.dumps(plan['summary'],ensure_ascii=False))


if __name__ == '__main__':
    main()
