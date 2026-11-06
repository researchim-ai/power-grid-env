"""Data-only saved sensitivity and protected-start workflows, local artifacts."""
from pathlib import Path
import html
from power_grid_env.study_artifacts import encoded,seal
from power_grid_env.report import write_json
from power_grid_env.evidence import package_fingerprint


def _new(out):
    out=Path(out)
    if out.exists():raise ValueError('new study output directory required')
    return out


def run_sensitivity_artifact(study,protocol,out):
    from power_grid_env.study_spec import StudySpec
    from power_grid_env.study_sensitivity import SensitivityRequest,differentiate_study
    out=_new(out);spec=StudySpec.model_validate(study);request=SensitivityRequest.model_validate(protocol);code=package_fingerprint()
    result=differentiate_study(spec,request)
    if code!=package_fingerprint():raise ValueError('sensitivity producer changed during study')
    encoded(result);out.mkdir(parents=True);write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'protocol.json',request.model_dump(mode='json'));write_json(out/'result.json',result);seal(out)
    return result


def protected_html(study,result):
    escape=lambda s:html.escape(str(s))
    e=result['engineering'];p=study.case.motors[0].passport
    rows=''.join('<tr><th>'+escape(k)+'</th><td>'+escape(v)+'</td></tr>' for k,v in e.items())
    events=''.join('<tr><td>'+escape(r['time_s'])+'</td><td>'+escape(r['kind'])+'</td><td>'+escape(r.get('outcome',''))+'</td><td>'+escape(r.get('causes',''))+'</td></tr>' for r in result['event_log'])
    sources=''.join('<li>'+escape(k)+': '+escape(v)+'</li>' for k,v in p.provenance.items())
    return ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>Пуск и защита двигателя</title><style>body{font:16px system-ui;max-width:1000px;margin:32px auto;padding:0 20px;color:#142336}table{border-collapse:collapse;width:100%;margin:20px 0}td,th{border:1px solid #ccd6e0;padding:8px;text-align:left}th{background:#edf3f8}h1,h2{color:#164a68}li{margin:8px 0}</style>'
        '<h1>Пуск и защита двигателя</h1><p>'+escape(study.case.id)+'</p><p>Расчёт завершён. Инженерный исход: <strong>'+escape(e['classification'])+'</strong>.</p>'
        '<h2>Результаты</h2><table>'+rows+'</table><p>Экстремумы относятся к экспортированным отсчётам. Ток RMS-equivalent вычислен из мгновенного αβ-вектора; это не измерение RMS за полный период.</p>'
        '<p>Минимальное напряжение за весь закрытый период включает заданный провал питания. Отдельный available-показатель оценивает напряжение при доступном питании по заданному трёхфазному монитору; оба результата сохранены.</p><h2>Уставки и применимость</h2><p>Реле: In='+escape(study.relay.rated_current_rms_a)+' А, τ='+escape(study.relay.time_constant_s)+' с, θtrip='+escape(study.relay.trip_level)+
        '. Закон: τ dθ/dt = (I/In)² − θ. Отключение через '+escape(study.relay.opening_delay_s)+' с. Уставки: '+escape(study.relay.provenance)+'.</p>'
        '<p>Контакты имеют конечное отключённое сопротивление '+escape(study.off_resistance_ohm)+' Ом и явные конденсаторы/пассивные ограничители. Порог ограничителя '+escape(study.clamp_knee_v)+' В. Токи и потокосцепления при отключении сохраняются; энергия рассеяния включена в баланс. Параметры не являются паспортом OEM или подтверждением натурного аппарата.</p>'
        '<h2>События</h2><table><tr><th>Время, с</th><th>Действие</th><th>Результат</th><th>Причина</th></tr>'+events+'</table>'
        '<h2>Происхождение параметров</h2><ul>'+sources+'</ul><p><a href="result.json">Полные каналы и энергетический баланс JSON</a> · <a href="study.json">Исходные данные</a></p></html>')


def run_protected_artifact(document,out):
    from power_grid_env.protected_motor import ProtectedMotorStudy,run_protected_motor
    out=_new(out);study=ProtectedMotorStudy.model_validate(document);code=package_fingerprint();result=run_protected_motor(study)
    if code!=package_fingerprint():raise ValueError('protected motor producer changed during study')
    encoded(result);out.mkdir(parents=True);write_json(out/'study.json',study.model_dump(mode='json'));write_json(out/'result.json',result)
    if result.get('completed'):
        write_json(out/'engineering.json',result['engineering']);(out/'report.html').write_text(protected_html(study,result),encoding='utf-8')
    seal(out);return result


def dynamic_identification_html(spec,result):
    import numpy as np
    escape=lambda v:html.escape(str(v))
    def plot(times,observed,predicted,label):
        # Display only is downsampled. Full fit/replay arrays remain in JSON.
        t=np.array(times);a=np.asarray(observed);b=np.asarray(predicted);indices=np.unique(np.linspace(0,len(t)-1,min(800,len(t)),dtype=int))
        lo=float(min(a.min(),b.min()));hi=float(max(a.max(),b.max()));span=max(hi-lo,1e-8)
        def points(values):return ' '.join(f'{40+580*t[i]/t[-1]:.3f},{145-120*(values[i]-lo)/span:.3f}' for i in indices)
        return '<svg role="img" aria-label="'+escape(label)+'" viewBox="0 0 640 180"><title>'+escape(label)+'</title><path d="M40 20V145H620" fill="none" stroke="#607080"/><polyline fill="none" stroke="#b54030" stroke-width="1" points="'+points(a)+'"/><polyline fill="none" stroke="#126ca0" stroke-width="1" points="'+points(b)+'"/><text x="40" y="173">0 s</text><text x="550" y="173">'+escape(t[-1])+' s</text><text x="45" y="15">'+escape(label)+' ['+escape(round(lo,5))+', '+escape(round(hi,5))+']</text></svg>'
    rows=''.join('<tr><td>'+escape(row['id'])+'</td><td>'+escape(row['split'])+'</td><td>'+escape(row['current_error_sigma'])+'</td><td>'+escape(row['speed_error_sigma'])+'</td><td>'+escape(row['passed'])+'</td></tr>' for row in result.get('assessments',[]))
    graphs=[]
    for trace in spec.fitting+spec.validation:
        if trace.id not in result.get('trajectories',{}):continue
        r=result['trajectories'][trace.id]
        graphs.append('<h3>'+escape(trace.id)+'</h3>'+plot(trace.time_s,trace.current_alpha_a,r['current_alpha_a'],'iα, A peak')+plot(trace.time_s,trace.speed_rad_s,r['speed_rad_s'],'ω, rad/s'))
    return '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Динамическая идентификация двигателя</title><style>body{font:16px system-ui;max-width:1000px;margin:30px auto;padding:0 20px;color:#173348}table{border-collapse:collapse;width:100%}td,th{border:1px solid #b9ccd9;padding:8px}svg{width:100%;max-width:640px}text{font:12px system-ui}</style><h1>Динамическая идентификация двигателя</h1><p>Принят: '+escape(result.get('passed',False))+'; идентифицируем: '+escape(result.get('identifiable',False))+'. '+escape(result.get('reason',''))+'</p><p>Электрический подбор использует измеренную скорость. Приёмка обязательно включает свободное электромеханическое воспроизведение тренировочных и отдельных проверочных трасс. J и коэффициент нагрузки оцениваются интегральным уравнением механики. Начальные потокосцепления заданы явно.</p><p>Красный — наблюдение, синий — свободный расчёт. Токи в peak αβ SI, скорость в rad/s; sigma — заданный масштаб ошибки, не статистическая гарантия. Синтетические наблюдения не являются натурными измерениями.</p><h2>Ошибки свободного воспроизведения</h2><table><tr><th>Трасса</th><th>Набор</th><th>Ток / sigma</th><th>Скорость / sigma</th><th>Принят</th></tr>'+rows+'</table><h2>Идентифицируемость</h2><pre>'+escape(dict(electrical=result.get('electrical'),mechanical=result.get('mechanical')))+'</pre><h2>Диапазон тренировочных данных</h2><pre>'+escape(result.get('calibration_domain',{}))+'</pre>'+''.join(graphs)+'<p><a href="study.json">Вход и происхождение данных</a> · <a href="result.json">Все результаты JSON</a></p></html>'


def run_dynamic_identification_artifact(document,out):
    from power_grid_env.dynamic_motor_identification import DynamicMotorIdentification,identify_dynamic
    out=_new(out);spec=DynamicMotorIdentification.model_validate(document);code=package_fingerprint();result=identify_dynamic(spec)
    if code!=package_fingerprint():raise ValueError('identification producer changed')
    encoded(result);out.mkdir(parents=True);write_json(out/'study.json',spec.model_dump(mode='json'));write_json(out/'result.json',result)
    (out/'report.html').write_text(dynamic_identification_html(spec,result),encoding='utf-8')
    if result.get('passed'):write_json(out/'passport.json',result['passport'])
    seal(out);return result
