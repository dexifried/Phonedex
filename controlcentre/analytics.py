"""Analytics preserve unknown costs and unknown task outcomes."""
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
import csv
import io
import math


def analytics(calls, days=30):
    """Undated records remain visible in totals, never fabricated into a bucket.

    `total_cost` is a compatibility alias for the sum of confirmed charges only.
    Coverage counts accompany all partial measurements. Completing a connection
    is never evidence that the operator's task succeeded.
    """
    current = datetime.now(timezone.utc)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    selected, dates = [], {}

    def numeric(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    def cost(call, kind):
        kinds = {'confirmed': {'confirmed', 'actual', 'billed'}, 'estimated': {'estimated', 'estimate'}}
        value = call.get('cost')
        return value if numeric(value) and call.get('cost_kind') in kinds[kind] else None

    for call in calls:
        date = None
        try:
            parsed = datetime.fromisoformat(str(call.get('started_at')).replace('Z', '+00:00'))
            # A missing timezone is unknown, rather than an invented local zone.
            if parsed.tzinfo is not None:
                date = parsed.astimezone(timezone.utc)
                if date < start or date > current:
                    continue
        except (TypeError, ValueError, OverflowError):
            pass
        dates[id(call)] = date
        selected.append(call)

    def summary(items):
        outcomes = [c['task_success'] for c in items if isinstance(c.get('task_success'), bool)]
        confirmed = [cost(c, 'confirmed') for c in items if cost(c, 'confirmed') is not None]
        estimated = [cost(c, 'estimated') for c in items if cost(c, 'estimated') is not None]
        durations = [c['duration_seconds'] for c in items if numeric(c.get('duration_seconds'))]
        successes = sum(outcomes)
        complete_costs_and_outcomes = len(confirmed) == len(items) == len(outcomes)
        return dict(total_calls=len(items),
                    active_calls=sum(c.get('state') in {'active', 'answered', 'connected', 'dialing', 'ringing', 'ending'} for c in items),
                    completed_calls=sum(c.get('state') in {'completed', 'ended'} for c in items),
                    successful_tasks=successes, failed_tasks=len(outcomes)-successes,
                    success_rate=successes / len(outcomes) * 100 if outcomes else None,
                    known_outcomes=len(outcomes), unknown_outcomes=len(items)-len(outcomes),
                    total_cost=sum(confirmed) if confirmed else None,
                    confirmed_cost=sum(confirmed) if confirmed else None,
                    estimated_cost=sum(estimated) if estimated else None,
                    average_confirmed_cost=sum(confirmed)/len(confirmed) if confirmed else None,
                    average_estimated_cost=sum(estimated)/len(estimated) if estimated else None,
                    confirmed_cost_calls=len(confirmed), estimated_cost_calls=len(estimated),
                    known_cost_calls=len(confirmed)+len(estimated), unknown_cost_calls=len(items)-len(confirmed)-len(estimated),
                    cost_per_completed_task=sum(confirmed)/successes if complete_costs_and_outcomes and successes else None,
                    average_duration=sum(durations)/len(durations) if durations else None,
                    known_duration_calls=len(durations), total_duration=sum(durations))

    result = summary(selected)
    result.update(days=days, timezone='UTC', unknown_date_calls=sum(dates[id(c)] is None for c in selected),
                  period_start=start.isoformat(), period_end=current.isoformat())
    dated = defaultdict(list)
    agents = defaultdict(list)
    hours = Counter()
    for call in selected:
        date = dates[id(call)]
        if date is not None:
            dated[date.date().isoformat()].append(call)
            hours[date.hour] += 1
        agents[(call.get('agent_id'), call.get('agent_name') or 'Unknown agent')].append(call)
    result['daily'] = []
    for offset in range(days):
        date = (start + timedelta(days=offset)).date().isoformat()
        day = summary(dated[date])
        result['daily'].append(dict(date=date, calls=day['total_calls'], **day))
    result['by_hour'] = [{'hour': hour, 'calls': hours[hour]} for hour in range(24)]
    result['by_agent'] = [dict(agent_id=key[0], agent_name=key[1], **summary(items)) for key, items in agents.items()]
    result['by_agent'].sort(key=lambda item: (-item['total_calls'], item['agent_name']))
    result['outcomes'] = [{'outcome': 'success', 'count': result['successful_tasks']},
                          {'outcome': 'failure', 'count': result['failed_tasks']},
                          {'outcome': 'unknown', 'count': result['unknown_outcomes']}]
    states = Counter(c.get('state', 'unknown') for c in selected)
    result['states'] = [{'state': state, 'count': count} for state, count in sorted(states.items())]
    errors = Counter(c['error_code'] for c in selected if isinstance(c.get('error_code'), str) and c['error_code'])
    result['errors'] = [{'code': code, 'count': count} for code, count in sorted(errors.items())]
    durations = [c['duration_seconds'] for c in selected if numeric(c.get('duration_seconds'))]
    boundaries = [(0, 30), (30, 60), (60, 120), (120, 300), (300, None)]
    result['duration_distribution'] = [dict(min_seconds=low, max_seconds=high,
        count=sum(value >= low and (high is None or value < high) for value in durations)) for low, high in boundaries]
    return result


def csv_export(calls):
    output = io.StringIO(newline='')
    columns = ['id', 'contact', 'number_masked', 'direction', 'agent_name', 'provider', 'state',
               'started_at', 'ended_at', 'duration_seconds', 'outcome', 'task_success', 'cost', 'cost_kind']
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for call in calls:
        row = {}
        for column in columns:
            value = call.get(column)
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
                value = "'" + value
            row[column] = '' if value is None else value
        writer.writerow(row)
    return output.getvalue()
