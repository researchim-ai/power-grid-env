"""Render static, bilingual publication examples from the curated TRAIN snapshot."""
from pathlib import Path
from bs4 import BeautifulSoup
from html import escape
import json
def tr(ru,en,tag='span',attrs=''):return f'<{tag} {attrs} data-en="{escape(en,quote=True)}">{ru}</{tag}>'
def svg(example,state):
 children={n:[] for n in example['nodes']};depth={example['source']:0}
 for line in example['lines']:children[line['from_node']].append(line['to_node'])
 order=[]
 def visit(n):
  if not children[n]:order.append(n)
  for child in children[n]:depth[child]=depth[n]+1;visit(child)
 visit(example['source']);x={n:55+i*330/max(1,len(order)-1) for i,n in enumerate(order)}
 def place(n):
  if children[n]:x[n]=sum(place(c) for c in children[n])/len(children[n])
  return x[n]
 place(example['source']);y={n:55+depth[n]*100 for n in example['nodes']};height=max(y.values())+65
 loads={r['id']:r['loading_pct'] for r in state['branches']};volts={r['id']:r['voltage_pu_min'] for r in state['nodes']}
 pieces=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 440 {height}" role="img"><title>TRAIN network: line loading and voltage</title><rect width="440" height="{height}" fill="#0d171f"/>']
 for line in example['lines']:
  a,b=line['from_node'],line['to_node'];mid=(y[a]+y[b])/2;v=loads[line['id']];color='#f68e7a' if v>100 else '#c6ef82' if line['id']==example['changed_line'] else '#91a4b0';mx=(x[a]+x[b])/2
  vertical=abs(x[a]-x[b])<1
  labelx=(x[b]-16 if x[b]>220 else x[b]+16) if vertical else mx
  labely=mid+4 if vertical else mid-7
  anchor=('end' if x[b]>220 else 'start') if vertical else 'middle'
  pieces.append(f'<path d="M{x[a]} {y[a]} V{mid} H{x[b]} V{y[b]}" stroke="{color}" stroke-width="2" fill="none"/><text x="{labelx}" y="{labely}" text-anchor="{anchor}" fill="{color}" stroke="#0d171f" stroke-width="5" paint-order="stroke" font-family="monospace" font-size="16">{escape(line["id"])} · {v:.1f}%</text>')
 for n in example['nodes']:
  color='#f68e7a' if volts[n]<.95 else '#a8c7ec'
  pieces.append(f'<circle cx="{x[n]}" cy="{y[n]}" r="10" fill="#172c38" stroke="{color}" stroke-width="2"/><text x="{x[n]}" y="{y[n]-17}" text-anchor="middle" fill="#e0eaf0" font-family="monospace" font-size="17">{escape(n)}</text><text x="{x[n]}" y="{y[n]+29}" text-anchor="middle" fill="{color}" stroke="#0d171f" stroke-width="3" paint-order="stroke" font-family="monospace" font-size="16">{volts[n]:.3f} pu</text>')
 return ''.join(pieces)+'</svg>\n'

TITLES = {
    'verify_healthy': ('Проверить исправную сеть и оставить её без изменений',
                       'Verify a healthy network and leave it unchanged'),
    'compare_replacements': ('Сравнить кабели расчётом и отклонить неподходящий',
                             'Compare cables with the solver and reject an unsuitable option'),
}


def evidence_trace(trace):
    html = '<details>' + tr(f'Действия модели: {trace["calls"]}',
                            f'Model calls: {trace["calls"]}', 'summary') + '<ol>'
    for turn in trace['trace']:
        html += '<li><code>' + escape(turn['raw']) + '</code>'
        if turn['decode_error']:
            html += tr('Вызов отклонён: неверный формат.', 'Call rejected: invalid format.',
                       'span', 'class="case-error"')
        html += '</li>'
    return html + '</ol></details>'


def preview_cards(example):
    html = '<div id="catalog-previews">' + tr('Как агент выбирал вариант', 'How the agent selected an option', 'h4')
    html += tr('Оба расчёта ниже — проверки гипотез. Они не меняли сеть. '
               'Агент применил второй вариант, пересчитал реальную сеть и только затем завершил задачу.',
               'Both studies below are hypothetical previews. Neither changed the network. '
               'The agent applied the second option, recomputed the actual network, then submitted.', 'p')
    html += '<div class="preview-decisions">'
    for preview in example['previews']:
        a = preview['assessment']
        feasible = a['measurement_feasible']
        html += '<section class="preview-decision">'
        html += tr('Подходит по результату расчёта' if feasible else 'Не проходит по загрузке',
                   'Feasible in the preview' if feasible else 'Fails the loading limit', 'h5')
        html += '<code>' + escape(preview['catalog_id']) + '</code>'
        html += '<dl><div>' + tr('Минимальное U', 'Minimum voltage', 'dt')
        html += f'<dd>{a["minimum_voltage_pu"]:.3f} pu</dd></div><div>'
        html += tr('Максимальная загрузка', 'Maximum line loading', 'dt')
        html += f'<dd>{a["maximum_branch_loading_pct"]:.1f}%</dd></div></dl>'
        p = preview['passport']
        html += tr(f'R = {p["r_ohm_km"]:.4f} Ом/км · допустимый ток {p["rating_a"]:.1f} А',
                   f'R = {p["r_ohm_km"]:.4f} ohm/km · current rating {p["rating_a"]:.1f} A', 'p')
        html += '</section>'
    return html + '</div>' + tr(
        'У первого кабеля меньше сопротивление, но слишком низкий допустимый ток: '
        'загрузка достигает 401,4%. Одного улучшения напряжения для выбора недостаточно.',
        'The first cable has lower resistance but an insufficient current rating: '
        'loading reaches 401.4%. Improving voltage alone is not enough to select it.', 'p',
        'class="small-note"') + '</div>'


def render(root):
    data = json.loads((root / 'assets/learning-examples.json').read_text())
    html = tr('Какие решения научилась принимать модель',
              'What decisions the model learned to make', 'h2')
    html += tr('В этом эксперименте было два типа задач: проверить исправную сеть '
               'и исправить сеть выбором кабеля из каталога. Разные длины линий и число узлов '
               'не превращают замену кабеля в новую инженерную задачу.',
               'This experiment had two task types: verify a healthy network and repair a network '
               'by selecting a cable from a catalogue. Different line lengths and bus counts '
               'do not make cable replacement a new engineering task.', 'p')
    html += '<div class="task-coverage">'
    for row in data['task_types']:
        ru, en = ('Проверка исправной сети', 'Healthy-network verification') if row['kind']=='verify_healthy' else ('Подбор кабеля для проблемной сети', 'Cable selection for a faulty network')
        html += '<div>' + tr(ru, en, 'strong')
        html += tr(f'Исходная 4B: {row["base_successes"]}/{row["total"]}. '
                   f'После SFT 240: {row["sft_successes"]}/{row["total"]}.',
                   f'Base 4B: {row["base_successes"]}/{row["total"]}. '
                   f'After SFT 240: {row["sft_successes"]}/{row["total"]}.', 'p') + '</div>'
    html += '</div>'
    html += tr('Это разбиение прежней регрессионной выборки из 30 случаев, не новый тест. '
               'Ниже — по одному выбранному TRAIN-примеру каждого типа. Все действия '
               'воспроизведены из сохранённых ответов модели, без подстановки сценарного контроллера.',
               'These are groups within the previously disclosed 30-case regression suite, not a new test. '
               'Below is one selected TRAIN example of each type. All actions were replayed '
               'from recorded model responses without substituting a scripted controller.', 'p',
               'class="small-note"')
    html += tr('Базовая модель ошибалась в формате вызовов и не выполнила расчёт. '
               'Показатели исходной сети получены первым расчётом обученной модели, до изменений. '
               'Размещение узлов условное; напряжения и загрузка проверены повторным расчётом.',
               'The base model used the wrong call format and did not run a study. '
               'Original network measurements come from the trained model’s first simulation, '
               'before any edits. Layout is schematic; voltages and loading were recomputed.', 'p',
               'class="small-note"')
    html += '<p class="case-jumps"><a href="#train-example-1">' + tr('Проверка без правок', 'Verification without edits') + '</a> · <a href="#train-example-2">' + tr('Подбор кабеля', 'Cable selection') + '</a> · <a href="#catalog-previews">' + tr('Сравнение вариантов', 'Comparing options') + '</a></p>'
    files = ['learning-examples.json']
    for i, example in enumerate(data['examples'], 1):
        healthy = example['kind'] == 'verify_healthy'
        ru, en = TITLES[example['kind']]
        html += f'<article class="learning-case" id="{example["id"]}">' + tr(ru, en, 'h3')
        html += tr(f'{len(example["nodes"])} узлов · 400 В · TRAIN · Qwen3.5-4B',
                   f'{len(example["nodes"])} buses · 400 V · TRAIN · Qwen3.5-4B', 'p',
                   'class="small-note"')
        html += tr('Сеть уже удовлетворяет ограничениям. Нужно подтвердить это расчётом '
                   'и завершить задачу, сохранив оборудование и нагрузки.' if healthy else
                   'Линия L4 перегружена. Разрешена одна замена кабеля; нагрузки и маршруты '
                   'должны сохраниться, напряжение — остаться не ниже 0,95 pu, загрузка — не выше 100%.',
                   'The network already meets its limits. Verify it with a study, then submit '
                   'without changing equipment or loads.' if healthy else
                   'Line L4 is overloaded. One cable replacement is allowed; loads and routes '
                   'must be preserved, voltage must stay at or above 0.95 pu and loading at or below 100%.', 'p')
        html += '<div class="case-comparison">'
        for phase, state, ru, en in [
            ('before', example['initial'], 'До обучения', 'Before training'),
            ('after', example['final'], 'После SFT · 240 шагов', 'After SFT · 240 updates'),
        ]:
            file = f'learning-example-{i}-{phase}.svg'
            (root / 'assets' / file).write_text(svg(example, state))
            files.append(file)
            stats = state['assessment']
            trace = example['base'] if phase == 'before' else example['sft']
            html += f'<section class="case-phase {phase}">' + tr(ru, en, 'h4')
            html += tr('Задача не решена' if phase == 'before' else 'Задача решена',
                       'Task failed' if phase == 'before' else 'Task completed', 'p',
                       'class="case-verdict"')
            html += f'<img src="./assets/{file}" width="440" height="{max(len(example["nodes"])*40,300)}" loading="lazy" alt="{ru}" data-en-alt="{en}">'
            html += '<dl class="case-metrics"><div>' + tr('Минимальное U', 'Minimum voltage', 'dt')
            html += f'<dd>{stats["minimum_voltage_pu"]:.3f} pu</dd></div><div>'
            html += tr('Максимальная загрузка', 'Maximum line loading', 'dt')
            html += f'<dd>{stats["maximum_branch_loading_pct"]:.1f}%</dd></div></dl>'
            if phase == 'before':
                html += tr('Исходная сеть; базовая модель не смогла вызвать решатель.',
                           'Original network; the base model could not call the solver.', 'p', 'class="case-caption"')
            elif healthy:
                html += tr('Расчёт выполнен, изменения не понадобились. Агент завершил задачу за два вызова.',
                           'Study completed; no changes were needed. The agent submitted in two calls.', 'p', 'class="case-caption"')
            else:
                html += tr('Кабель заменён после двух проверок вариантов. Итог подтверждён расчётом реальной сети.',
                           'Cable replaced after two hypothetical studies. A fresh actual study confirmed the result.', 'p', 'class="case-caption"')
            html += evidence_trace(trace) + '</section>'
        html += '</div>'
        if not healthy:
            html += preview_cards(example)
        else:
            html += tr('Схема после проверки выглядит так же — это правильный результат для исправной сети. '
                       'Успех здесь означает выполнение расчёта и завершение задачи, а не обязательную правку.',
                       'The verified diagram is unchanged: that is the correct outcome for a healthy network. '
                       'Success means running the study and submitting, not making a mandatory edit.', 'p')
        html += '<p class="case-download"><a download href="./assets/' + example['project_file'] + '">'
        html += tr('Исходная сеть (JSON)', 'Original network (JSON)') + '</a> · <a download href="./assets/'
        html += example['modified_project_file'] + '">' + tr('Итоговая сеть (JSON)', 'Final network (JSON)') + '</a></p></article>'
        files.extend([example['project_file'], example['modified_project_file']])
    html += '<div class="callout scope-box">' + tr('Каких задач в этом эксперименте не было',
                                                  'Tasks outside this experiment', 'h3')
    html += tr('Сборка сети по ТЗ и диагностика внесённых проблем есть в отдельных ограниченных '
               'эпизодах Agent Lab, но их не проверяли этим SFT-прогоном. Переключения, координация защит, '
               'пуск двигателей и чтение схем по изображению также не входят в приведённые результаты. '
               'Для каждого направления нужны свои данные и отдельная оценка агента.',
               'Bounded network assembly and injected-fault diagnosis episodes exist separately in Agent Lab, '
               'but were not evaluated in this SFT run. Switching, protection coordination, motor starting '
               'and reading diagrams from images are also outside these results. Each direction needs '
               'its own data and agent evaluation.', 'p') + '</div>'
    html += '<p><a download href="./assets/learning-examples.json">' + tr(
        'Скачать данные примеров и ссылки на исходные артефакты',
        'Download example data and source artifact references') + '</a></p>'
    path = root / 'blog-training.html'
    soup = BeautifulSoup(path.read_text(), 'html.parser')
    section = soup.new_tag('section', id='examples')
    for child in list(BeautifulSoup(html, 'html.parser').contents):
        section.append(child.extract())
    existing = soup.select_one('#examples')
    if existing:
        existing.decompose()
    for old in soup.select('.article-toc a[href="#examples"]'):
        old.decompose()
    soup.select_one('#results').insert_after(section)
    link = soup.new_tag('a', href='#examples')
    for child in list(BeautifulSoup(tr('Примеры решений', 'Decision examples'), 'html.parser').contents):
        link.append(child.extract())
    soup.select_one('.article-toc a[href="#results"]').insert_after(link)
    path.write_text(str(soup).rstrip() + '\n')
    path = root / 'site-config.js'
    text = path.read_text()
    import re
    for file in files:
        if repr(file) not in text:
            text = re.sub(r'(export const SITE_ASSETS=\[)(.*?)(\];)',
                          lambda m: m[1] + m[2] + ',' + repr(file) + m[3], text, count=1)
    path.write_text(text)


if __name__ == '__main__':
    render(Path(__file__).resolve().parents[1] / 'web')
