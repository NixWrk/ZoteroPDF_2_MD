# План доработок RU HTML (Wang 2017 LC Sensor) — Phase 4+

> **Источник evidence:** прогон 2026-04-20 13:54, лог
> `logs/translate_wang_full_20260420_135423.log` (126 LLM-вызовов, 357/357
> progress-сегментов, `TRANSLATED_SEGMENTS=308`, 2068 s),
> RU HTML: `md_output/intracranial/Wang и др. - 2017 - A Novel Intracranial Pressure Readout Circuit for Passive Wireless LC Sensor/Wang и др. - 2017 - A Novel Intracranial Pressure Readout Circuit for Passive Wireless LC Sensor.ru.html` (1809 строк, 813 KB).

## 1. Контекст

После реализации id-протокола v2 (Phase 1, коммит `4a43d65`), cascade-hardening
(Phase 2, `e8a7435`) и sentinel-протокола для аббревиатур/формул
(Phase 3, коммиты `5b86567`…`66bb78e`) глобального fallback на Wang больше
нет: `debug_translate_stop_on_fallback.py` проходит чисто.

**Однако** текущий прогон 20 апреля показал, что *отсутствие срабатываний
`window_failed` в логе ещё не означает отсутствия повреждений*. Cascade стал
«тихо успешным»: id-парсер принимает окно, а фактический RU-контент в
отдельных батчах испорчен.

Этот документ — ведущий backlog для Phase 4. Он фиксирует все подтверждённые
грепом/чтением дефекты текущего RU HTML, их предполагаемые корневые причины
(с указанием файла и функции) и минимальный набор фиксов.

---

## 2. Подтверждённые дефекты и их корневые причины

### D1. Leak id-маркера `<z2m-i7>` в тело RU HTML  *(CRITICAL)*

**Файл:** строка 415.

```
... which is discussed in (<z2m-i7>-D), we solved the complicated
series-parallel circuit equations. <a href="#section-III">Section III</a>-D),
we solved the complicated series-parallel circuit equations.
<a href="#fig-6">Fig. 6</a> shows that adding equivalent resistor
```

Находки грепом:
- `grep -c "<z2m-"` = 1 (единственное вхождение во всём документе).
- На той же строке дубль `we solved the complicated series-parallel circuit equations.` (2 вхождения подряд).
- Перед дублем — длинный англ. непереведённый фрагмент `Given the above stated value chosen … resistor`.

**Корневая причина.** В `src/zoteropdf2md/translategemma.py`:

- `_try_batch_translate_with_reason` (~строка 648) и
  `_try_windowed_batch_translate_with_reason` (~строка 761) при reassembly
  окна проверяют только **количество** распознанных id-маркеров (`<z2m-iN/>`).
  Если модель выдала `<z2m-i7>-D` (без `/`), парсер засчитывает маркер как
  «присутствует», делит буфер по `<z2m-i7>`, и ХВОСТ `-D), we solved ...`
  остаётся в теле сегмента, в который попал маркер. Параллельно тот же
  фрагмент уже существует в соседнем «корректном» сегменте → получаем дубль +
  leak маркера одновременно.
- Reassembly не содержит post-check: «в собранном выходе НЕТ `<z2m-…`».

**Влияние.** В документе сейчас:
1. Просочившийся HTML-маркер (визуально `<z2m-i7>` в браузере интерпретируется как пустой неизвестный tag, но в исходнике это шум).
2. Дублированный переводный сегмент.
3. Полностью непереведённый англоязычный кусок.

---

### D2. Несогласованность перевода caption'ов таблиц  *(HIGH)*

**Находки:** `TABLE I PARAMETERS FOR TWO ANTENNAS` (строка 487) и
`TABLE IV COMPARISON OF STATE OF ARTS` (строка 1191) остались **полностью**
по-английски. В то же время `TABLE II ПАРАМЕТРЫ ДАТЧИКА` (601) и
`TABLE III Параметры антенны` (917) переведены частично.

**Корневая причина.** Caption'ы в Marker-HTML — это `<p>TABLE N … </p>` перед
`<table>`, не внутри `<caption>`. Они попадают в общий поток `parts`, но:

- префикс `TABLE I/II/III/IV` иногда игнорируется моделью (воспринимается
  как уже переведённый «шум заголовка»), иногда целиком перефразируется, а
  иногда оставляется как есть. Поведение зависит от соседей в батче.
- в `translate_html_text_nodes` (`translategemma.py:1410`) эти `<p>`-узлы не
  обрабатываются никак особо, хотя их структура стабильна: `TABLE\s+[IVX]+\s+<UPPERCASE TEXT>`.

**Влияние.** Вперемешку RU и EN заголовки таблиц → читатель вынужден
переключать язык, UX плохой.

---

### D3. Кривой перевод заголовка статьи (h1)  *(HIGH)*

**Файл:** строки 157–162.

```html
<h1>
 Новая схема считывания внутричерепного давления для пассивной беспроводной
 системы <i>LC</i> датчик
</h1>
```

**Ожидалось:** «Новая схема считывания внутричерепного давления для
пассивного беспроводного LC-датчика».

**Корневая причина.** Уже была проанализирована как BUG-B в архивной части
`cozy-wiggling-reddy.md`: `<h1>` разрезается `<i>LC</i>` на три текстовых
узла (`"…Passive Wireless\n   "`, `"LC"`, `"Sensor\n  "`). Модель переводит
каждый узел изолированно → правильный падеж и согласование невозможны.

Также в текущем виде `датчик` стоит в именительном падеже после
словосочетания в родительном → грамматический конфликт.

**Влияние.** Заголовок — визитная карточка документа. Плохой перевод здесь
бросается в глаза сразу при открытии.

---

### D4. Склейка ссылки на библиографию со следующим словом  *(MEDIUM)*

**Файл:** строка 1188:

```
… электромагнитными помехами. <a href="#ref-30" class="z2m-ref-link">[30]</a>Мы
применили цифровой фильтр …
```

После `</a>` сразу идёт кириллица без пробела: `[30]Мы применили …`.

**Корневая причина.** В EN-источнике `[30]` примыкает к точке и тексту без
пробела: `emf. [30] We applied…`. При переводе разделитель между узлами
(`<z2m-sep/>` или его ASCII-аналог) стирается модели, и результат склеивается.
`single_file_html.py` не нормализует пробелы после `</a class="z2m-ref-link">`.

**Влияние.** Ухудшение типографики, но не разрушение контента. Лечится
пост-нормализатором (аналог `</sup>[А-Яа-я]` из старого плана).

---

### D5. Микс EN/RU внутри одного предложения, обрыв на «Мы определяем…»  *(CRITICAL)*

**Файл:** строки 415–419 — то же «окно», что D1:

- 415: английский фрагмент + leak маркера + дубль.
- 417: `…<a href="#fig-4">Fig. 4</a>, в серии всегда уменьшается` — сначала
  EN контекст, потом резкий переход на RU.
- 419: `когда диапазон резонансной частоты составляет от 35 МГц до 2,7 ГГц.
  Мы определяем...` — финальное «Мы определяем…» оборвано многоточием
  (должно было продолжаться).

**Корневая причина.** Тот же батч, что в D1. Когда модель сломала один
маркер (см. D1), связанные сегменты того же окна получили смещение
контента: часть EN-исходника перетекла в чужой слот, часть RU-перевода
обрезалась. Это **структурный эффект** единственного leak'а id-маркера.

**Влияние.** Минимум 5 сегментов в одном абзаце нечитаемы.

---

### D6. Расхождение `TRANSLATED_SEGMENTS=308` при progress 357/357  *(OBSERVABILITY)*

**Источник:** stdout прогона + `translate_html_file` → возвращает
`translated_segments` (`translategemma.py:1926`, счёт в
`translate_html_text_nodes` строки 1419/1471).

- 19 апреля: `translated_segments=338`.
- 20 апреля: `translated_segments=308`, при том же progress 357/357.

**Гипотеза корневой причины.** При `marker_leak`/`duplicate_leak`
фактическое число применённых переводов меньше, чем число батченных
сегментов. Сейчас нет телеметрии, показывающей *почему* 49 сегментов не
засчитаны — они могли быть whitespace/short-circuit или проглочены id-парсером.

**Влияние.** Нет сигнала раннего обнаружения тихой деградации. Добавить
warning-лог, если `translated_segments / len(parts) < 0.85`.

---

## 3. План фиксов (Phase 4)

### P4.1 Post-reassembly guards в `translategemma.py`

После reassembly батча — до возврата `translated` — прогонять три guard'а.
Любое срабатывание → пометить батч `leak_recovery`, уйти в локальный
`leaf-per-segment` (тот же путь, что `window_failed` после Phase 2).

| Guard | Условие | Имя reason |
|-------|---------|-----------|
| **G1 Marker-leak** | В любом `translated[i]` есть `<z2m-(i\d+|sep|end)` или ASCII-sentinel аббревиатуры/формулы | `marker_leak` |
| **G2 Duplicate-neighbor** | `len(translated[i]) > 40` и первые/последние 30 символов совпадают с `translated[i-1]` или `translated[i+1]` | `duplicate_leak` |
| **G3 Language-identity** | target=`ru`, `len(translated[i]) > 20`, доля латинских букв ≥ 80 % при ≥ 5 словах | `identity_residual` |

**Места вставки:**

- `_try_batch_translate_with_reason` (~строка 648–758): guard-блок после
  успешного парса id-маркеров, перед `return translated, "ok", …`.
- `_try_windowed_batch_translate_with_reason` (~строка 761–820): тот же блок
  перед возвратом `translated`.

**Важно:** guard'ы запускаются даже на «чистом» ok-пути, а не только в
fallback-ветках.

### P4.2 Coverage-warning

В `translate_html_text_nodes` (строки 1410–1484) добавить лог:

```python
if translated_segments < 0.85 * translatable_count:
    log(f"[WARN] coverage={translated_segments}/{translatable_count} "
        f"({translated_segments/translatable_count:.1%}) — possible silent loss")
```

### P4.3 Нормализатор пробелов после `</a class="z2m-ref-link">` и `</a class="z2m-fig-link">`

В `single_file_html.py` (полиш-этап, рядом с `_fix_heading_translation_breaks`
и существующим sup-нормализатором из Commit 4 старого плана) добавить:

```
</a\s+class="z2m-(ref|fig|section)-link"\s*>([А-Яа-яЁё])
  → </a> \2
```

### P4.4 Heading merge (BUG-B) — перенести из архива

Реализовать pre-merge `<h1>`–`<h6>` из архивного раздела
`cozy-wiggling-reddy.md`:

1. В обходе `parts[]` отслеживать вход/выход в `<hN>` через стек тегов.
2. Если внутри `<hN>` ≥ 2 транслируемых узла — объединять в один сегмент
   через ASCII-sentinel (`<<Z2M_HSEP>>` или уже существующий heading
   sentinel из коммита `e8efbcf`).
3. После перевода — разрезать по sentinel'у и раздать обратно узлам.
4. Если sentinel потерян — оставить узлы в EN-оригинале (никакого merge-фолбэка-как-попало).

### P4.5 Специальный handler для caption `TABLE N …`

В `translate_html_text_nodes`:

1. При формировании батчей — `<p>TABLE\s+[IVX]+\s+[A-Z].*</p>` вставлять в
   сегмент, окружая префикс `TABLE N` плейсхолдером `<<Z2M_TAB_PREFIX_N>>`
   (не переводится), остальной UPPERCASE-текст — через отдельный sentinel.
2. После перевода восстанавливать `TABLE N` и понижать регистр
   переведённого хвоста на «предложный» стиль заголовка.

### P4.6 Telemetry уровня cascade

В логе cascade (коммит `1f721f1` ввёл debug traces) к каждому окну
добавлять:

```
[cascade] window=[a:b) core=[c:d) ids=ok leak=marker_leak|duplicate_leak|identity_residual|none
```

Это позволит post-factum (grep по логу прогона) видеть, где сработал guard,
и убедиться, что `debug_translate_stop_on_fallback.py` ловит эти reason'ы
через `on_batch_fallback(reason)`.

---

## 4. Тесты (`tests/test_translategemma_html.py`)

Добавить три unit-теста + один регресс:

| Тест | Fake `translate_text` | Ожидание |
|------|-----------------------|----------|
| `test_guard_marker_leak_recovers` | В одном сегменте возвращает `"… (<z2m-i7>-D) …"` | В выводе `<z2m-` отсутствует, контент восстановлен через leaf-per-segment |
| `test_guard_duplicate_neighbor_recovers` | Возвращает `[S, S, T]` (дубль соседа) | Вывод — `[S, S', T]` где S' получен per-segment |
| `test_guard_identity_residual_recovers` | Возвращает латинский текст для русского target | Вывод — реальный RU-перевод (через per-segment) |
| `test_coverage_warning_emits_once` | Возвращает пустые строки для 30 % сегментов | В лог попадает `[WARN] coverage=…` |

Плюс end-to-end регресс:
`debug_translate_stop_on_fallback.py` по Wang → `grep -c '<z2m-i' RU.html == 0`.

---

## 5. Критические файлы

| Файл | Изменения |
|------|-----------|
| `src/zoteropdf2md/translategemma.py` | P4.1 (guards в двух функциях), P4.2 (coverage warn), P4.4 (heading merge), P4.5 (table caption), P4.6 (telemetry) |
| `src/zoteropdf2md/single_file_html.py` | P4.3 (нормализатор пробелов) |
| `tests/test_translategemma_html.py` | Все тесты из §4 |
| `debug_translate_stop_on_fallback.py` | Убедиться, что `on_batch_fallback(reason)` принимает новые reason-коды и не крашит прогон на них (сейчас он бросает RuntimeError — для Phase 4 это ок, именно так мы и хотим ловить регрессии). |

---

## 6. Верификация (end-to-end)

1. `pytest tests/test_translategemma_html.py -q` — зелёно.
2. `pytest -q` — без регрессов по старым тестам.
3. `debug_translate_stop_on_fallback.py --html <Wang EN> …`:
   - В RU выходе: `grep -c '<z2m-' == 0`.
   - `grep -c 'series-parallel circuit equations' == 1` (не 2).
   - Строка «Given the above stated value…» — по-русски.
   - Строка 158 (`<h1>`): содержит «LC-датчика» или «беспроводного LC-датчика», без падежной ошибки.
   - `TABLE I…` и `TABLE IV…` — переведены (или оставлены с префиксом `TABLE N` + RU-хвост).
   - После `</a class="z2m-ref-link">` — либо пробел, либо знак препинания, НЕ прямо кириллица.
4. В логе прогона — либо **нет** строк `[cascade]… leak=…`, либо такие окна сопровождаются `leaf_per_segment` и не роняют документ.
5. `TRANSLATED_SEGMENTS` ≥ 85 % от `progress total`. При меньшем — разобрать в лог-предупреждениях.

---

## 7. Приоритезация

| Приоритет | Пункт | Обоснование |
|-----------|-------|-------------|
| P0 (сейчас) | P4.1 (G1/G2/G3), тесты из §4, верификация 1–3 | Устраняет критические D1+D5 |
| P1 | P4.2, P4.6 | Ранняя сигнализация — нужна до полноценных релизов |
| P1 | P4.3 | Мелкий, но видимый дефект D4 |
| P2 | P4.4 (heading merge) | D3 — качество заголовка, не разрушение |
| P2 | P4.5 (table captions) | D2 — UX, не поломка |

P0 делается одним коммитом с тестами. P1 и P2 — отдельными коммитами, в
порядке, указанном в таблице.

---

## 8. Phase 5 — остаточные дефекты (прогон 2026-04-20 19:15)

**Лог:** `logs/translate_wang_full_20260420_191536.log` (150 LLM-вызовов, 357/357 segments, `TRANSLATED_SEGMENTS=314`, 2428 s).
**RU HTML:** тот же `md_output/.../Wang.../Wang....ru.html`.

### 8.1 Статус исходных дефектов D1–D6 после коммитов `22ea8bf` (P4 P0) и `79e937b` (P4 P1+P2)

| Дефект | 13:54 | 16:50 (P0) | 19:15 (P1+P2) | 08:57 21 Apr (P5+P5.5) |
|---|---|---|---|---|
| D1 `<z2m-i\d+>` leak | 1 | 0 ✅ | 0 ✅ | 0 ✅ |
| D2 EN caption таблиц | 2/4 EN | 2/4 EN | 0/4 EN ✅ | 2/4 EN ⚠️ (`TABLE II/III` — см. F6) |
| D3 h1 падеж (`LC датчик`) | есть | есть | `LC-датчика` ✅ | `LC-датчика` ✅ |
| D4 склейка `</a>буква` | есть | есть | 0 ✅ | 0 ✅ |
| D5 дубль + EN residual | есть | 0 ✅ | 0 ✅ | 0 ✅ |
| coverage `translated/total` | 86% | 87% | 88% | 88% (316/357) |
| cascade `FALLBACK` глобальный | 0 | 0 | 0 | 0 |
| `[cascade]` events в логе | n/a | n/a | 0 | **0 ⚠️ (см. F5)** |

**E-серия — статус после P5+P5.5 (прогон 08:57):**

| Дефект | Статус | Локация |
|---|---|---|
| E1 ellipsis перед ссылкой | ❌ сохраняется | строка 420: `(...<a href="#section-III">…` |
| E2 trailing `...` | ❌ сохраняется | строки 387, 424 |
| E3 caption style | ⚠️ частично: I✅ IV✅, II❌ III❌ | строки 605, 921: `TABLE II/III` |
| E6 heading EN | ❌ сохраняется | строка 468: `B. RF Signal Generator…` |
| E7 para EN (ADF4351) | ❌ сохраняется | строки 464, 475 |
| E8 mixed EN/RU | ❌ сохраняется | область строк 460–465 |

**Телеметрия прогона 08:57:**
`[timer] translategemma.recovery_calls: total=100 time=302.99s avg=3.03s` — 100 recovery-вызовов, 303 s из 1570 s (≈19%). `[cascade]` debug-строки — 0 в логе (см. F5).

**Cascade-телеметрия (93 события):**

- `abbrev_tokens_altered → local_segment_recovery`: 35
- `identity_residual → local_segment_recovery`: 40
- `marker_leak → local_segment_recovery`: 3
- `duplicate_leak`: 0
- `id_mismatch` + `window_fail` + `structured_parse_failed` + `leaf_per_segment`: 15

### 8.2 Новые дефекты Phase 5 (E-серия)

#### E1. Ellipsis перед восстановленной ссылкой  *(HIGH)*

**Файл.** RU HTML строка 419:
```
… которая обсуждается в (...<a href="#section-III" class="z2m-section-link">Section III</a>-D), мы решили …
```
Исходник EN строка 417 — корректная структура `(<a href="#section-III">Section III</a>-D)`.

**Корневая причина.** При срабатывании guard G1 `marker_leak` (P4.1, commit
`22ea8bf`) окно уходит в `local_segment_recovery` → сегмент с потерянным
`<z2m-iN/>` (placeholder для `<a href>`) ретранслируется через
`_translate_text_segment(core_text)` **без placeholder'а для самого тега**.
Модель, видя `… discussed in (` с открытой скобкой и без контекста,
закрывает её как `(...)`. Далее собиратель вставляет оригинальный
`<a href="#section-III">` рядом, давая `(...<a>Section III</a>-D)`.

**Фикс P5.1 — placeholder-aware segment recovery.** Добавить в
`src/zoteropdf2md/translategemma.py`:

1. Пара функций `_apply_tag_mask(text) -> (masked_text, tag_map)` и
   `_restore_tag_mask(masked_text, tag_map)` по шаблону
   `_apply_abbrev_mask`/`_restore_abbrev_mask`
   (`translategemma.py:484-523`), использующие ASCII-sentinel
   `@@Z2M_T{n}@@`.
2. Во всех путях, которые делают single-segment retry внутри cascade
   (`_try_batch_translate_with_reason`,
   `_try_windowed_batch_translate_with_reason`,
   `_apply_post_reassembly_guards` — см. commit `22ea8bf`), прогонять
   core_text через `_apply_tag_mask` перед вызовом модели и через
   `_restore_tag_mask` после.
3. Если какой-то sentinel не вернулся из модели — **не** писать `...` и не
   молча выкидывать тег, а вставить оригинальный тег из `tag_map` на
   ожидаемую позицию (в конец или через эвристику соседей).
4. Telemetry: добавить reason `tag_mask_dropped` в `[cascade]` log, чтобы
   отслеживать потерю sentinel'ов модели.

#### E2. Модель дорисовывает «…» в конце обрывка  *(HIGH)*

**Файл.** RU HTML строка 423:
```
… когда диапазон резонансной частоты составляет от 35 МГц до 2,7 ГГц. Мы определяем...
```
В EN-источнике предложение НЕ заканчивается `...`: продолжается формулой
`\(V_{match}\)` в следующем text node.

**Корневая причина.** G3 `identity_residual` не срабатывает (контент
русский). Нет guard'а на трейлинг-троеточие, которого нет в source text
node. Модель сама дорисовывает `...` как «тут что-то ещё было».

**Фикс P5.2 — guard G4 `trailing_ellipsis_artifact`.** В
`_apply_post_reassembly_guards` (`translategemma.py`, после блока P4.1):

```
for i, (src, out) in enumerate(zip(sources, translated)):
    out_rstr = out.rstrip()
    src_rstr = src.rstrip()
    src_has = src_rstr.endswith(("...", "…"))
    out_has = out_rstr.endswith(("...", "…"))
    if out_has and not src_has and _has_following_translatable(parts, segment_index_of[i]):
        recover(i, reason="trailing_ellipsis_artifact")
```

Если single-segment retry снова возвращает `...` — fallback на identity
(исходный text node), чтобы не засорять RU. Утилита
`_has_following_translatable(parts, idx)` — проверка наличия
translatable text node после текущего в `parts[]` (внутри того же
предложения).

#### E3. Caption-стиль: нет точки после номера и капитализации  *(MEDIUM)*

**Файл.** RU HTML строки 491, 604, 920, 1194:
```
Таблица I параметры для двух антенн
Таблица II ПАРАМЕТРЫ ДАТЧИКА
Таблица III Параметры антенны
Таблица IV сравнение передовых методов
```

Ожидаемый единый стиль:
```
Таблица I. Параметры для двух антенн.
Таблица II. Параметры датчика.
Таблица III. Параметры антенны.
Таблица IV. Сравнение передовых методов.
```

**Корневая причина.** Commit `79e937b` добавил recovery caption (они
больше не остаются по-английски), но **не нормализует стиль**:
результирующий регистр зависит от регистра EN-caption
(`PARAMETERS FOR` → `параметры для` нижним, `PARAMETERS FOR` с сохранением
некоторых слов — смешанный).

**Фикс P5.3 — `_normalize_table_caption_style`** в
`src/zoteropdf2md/single_file_html.py`, в полиш-пайплайне рядом с
`_add_table_anchors` (commit `79e937b`):

1. Regex: `<p([^>]*)>\s*(Таблица|Table)\s+([IVX]+|\d+)\s*\.?\s*([^<]+?)\s*</p>`.
2. Обработка группы 4 (хвост):
   - `tail = tail.strip().rstrip(".").strip()`
   - `tail = tail.lower()` (чтобы погасить UPPERCASE)
   - `tail = tail[:1].upper() + tail[1:]` (capitalize first)
   - Добавить `.` в конце если нет.
3. Собрать `<p{attrs}>Таблица {N}. {tail}.</p>`.
4. Опционально — обернуть весь caption в `<strong>` по аналогии с
   `Рис. N.` (посмотреть `_add_figure_anchors`/`_FIG_CAPTION_PARA_PATTERN`
   в том же файле).

#### E4. Heading merge для ≥2 inline-тегов в h1-h6  *(LOW)*

В Wang единственный h1 с `<i>LC</i>` **закрыт** коммитом `79e937b`.
Остальных заголовков с inline-разметкой в корпусе нет. **Не планируется
отдельный фикс**, только регресс-тест `tests/test_translategemma_html.py`
на multi-inline heading, чтобы зафиксировать поведение.

#### E5. Время прогона выросло на 32% (1835 s → 2428 s)  *(OBSERVABILITY)*

При равном числе LLM-вызовов (150 vs 150) текущий прогон длится дольше.
Вероятная причина — single-segment retry внутри `local_segment_recovery`
делает дополнительный forward pass, и число recovery-событий выросло
(48 → 78). Это не рубит функциональность, но теряет время.

**Фикс P5.4 (low priority).** В `translategemma.py` завести счётчики
`recovery_calls_total` и `recovery_calls_time_s`, логировать в конце
файла. Если >20% времени уходит на recovery — объединять
recovery-сегменты в один forward pass (batch recovery).

### 8.3 Критические файлы (Phase 5)

| Файл | Пункт | Что делаем |
|---|---|---|
| `src/zoteropdf2md/translategemma.py` | P5.1 | `_apply_tag_mask`/`_restore_tag_mask` (sentinel `@@Z2M_T{n}@@`), подключить ко всем single-segment retry путям |
| `src/zoteropdf2md/translategemma.py` | P5.2 | Guard G4 `trailing_ellipsis_artifact` в `_apply_post_reassembly_guards` + утилита `_has_following_translatable` |
| `src/zoteropdf2md/translategemma.py` | P5.4 | Счётчики recovery calls / time в translator-объект, лог на finish |
| `src/zoteropdf2md/single_file_html.py` | P5.3 | `_normalize_table_caption_style` в полиш-пайплайне рядом с `_add_table_anchors` |
| `tests/test_translategemma_html.py` | P5.1, P5.2, E4 | 3 unit-теста: tag_mask сохраняет `<a href>` в recovery; G4 ловит dangling `...`; multi-inline `<h1>` regression |
| `tests/test_single_file_html.py` | P5.3 | 1 тест: 4 варианта caption → единый `Таблица N. Слово.` |

### 8.4 Приоритет и порядок

- **P5.1 + P5.2** — один коммит (связаны: без P5.1 на месте маркера будет `...`, без P5.2 «обрыв предложения» останется). Закрывает E1 и E2.
- **P5.3** — отдельный коммит. Закрывает E3.
- **E4** — регресс-тест в том же коммите, что и P5.1/P5.2.
- **P5.4** — отдельный коммит (observability), не блокирует другие.

### 8.5 Верификация Phase 5

1. `pytest tests/test_translategemma_html.py tests/test_single_file_html.py -q` — зелёно.
2. Прогон Wang:
   - `grep -c '(\.\.\.'` — 0 (нет `(…` перед ссылкой).
   - `grep -nE '[а-я]\.\.\.$'` — только там, где source кончается на `...`.
   - `grep -cE 'Таблица\s+[IVX]+\.\s+[А-Я]'` — **4** (все caption'ы нормализованы).
3. Лог-прогона: появились `reason=trailing_ellipsis_artifact`, при `marker_leak` — нет `(...)` в RU-тексте соседних строк.
4. `pytest -q` — старые тесты без регрессов.

---

## 8.6 Phase 5.5 — EN-residual в heading и абзацах (прогон 2026-04-20 19:15, доп. аудит)

**Свидетельства из RU HTML:**

| Локация | Что осталось |
|---|---|
| строка 467 | `<h2><i>B. RF Signal Generator and Microcontroller</i></h2>` — единственный text node внутри `<i>`, целиком EN |
| строки 462–464 | `<p>in which the sensor's resonant frequency located. An RF amplifier enhances the RF signal…</p>` — весь `<p>` EN |
| строки 473–475 | `<p>An ADF4351 chip (Analog Devices) is the main component for the RF signal generator…</p>` — весь `<p>` EN |
| строка 491 (абзац Fig. 8) | Смешанный EN/RU: `… A half-wave rectifier recovers the envelope amplitude. The amplitude value is sent back … Fig. 8 показывает, как программное обеспечение MATLAB обрабатывает данные о сигнале.` — часть сегментов EN, часть RU |

Все три кластера — **один корневой gap**: guard G3 `identity_residual`
(P4.1, commit `22ea8bf`) не ловит эти сегменты. По телеметрии 19:15 он
сработал 40 раз, но в этих конкретных местах — молчал. Причины
различаются по локации.

### E6. Heading-single-inline остался EN  *(HIGH)*

**Файл:** `Wang....ru.html` строка 467.

**Корневая причина.**
- P4.4 heading-merge (`_merge_heading_text_nodes`, `translategemma.py:1386-1455`)
  срабатывает только при **≥2** translatable text nodes внутри `<hN>`.
  Здесь один узел.
- Одиночный узел идёт обычным batched-путём через
  `translate_html_text_nodes`.
- G3 проверяется в `_apply_post_reassembly_guards` и **должен** был
  увидеть: latin_ratio=1.0, ≥5 слов, len=41. Но guard пропустил.
  Вероятная причина — heading-сегменты после `_split_heading_text_nodes`
  (`translategemma.py:1458-1504`) собираются **после** вызова
  `_apply_post_reassembly_guards` для окна; повторная валидация не
  запускается. Либо путь, по которому heading-узел отдельно проходит
  через batch, не делится на окна вовсе и guard в нём не вызывается.

**Фикс P5.5.1 — post-heading identity recheck.** В
`translate_html_text_nodes` (`translategemma.py:~1410-1484`) после
реассемблинга всех сегментов добавить финальный проход:

```python
for idx, part in enumerate(parts):
    if part.kind != "text":
        continue
    out = translated_output[idx]
    src = part.text
    if _is_identity_residual(src, out, target_lang="ru"):
        recovered = _translate_text_segment(src)  # with tag_mask from P5.1
        if not _is_identity_residual(src, recovered, target_lang="ru"):
            translated_output[idx] = recovered
        # else: оставить EN — лучше честный EN, чем зацикливание
```

`_is_identity_residual` — новая утилита: `latin_ratio ≥ 0.8` ИЛИ
`translated.strip() == source.strip()` при target_lang=`ru` и в source
≥ 2 латинских слов (т.е. это не одиночная аббревиатура, а фраза).

**Критерий:** `<h2><i>B. RF Signal Generator and Microcontroller</i></h2>`
в RU HTML превращается в `<h2><i>B. Радиочастотный генератор сигнала и
микроконтроллер</i></h2>` (или сохраняет префикс `B.` и переводит хвост).

### E7. Целый абзац вернулся EN (paragraph-level identity)  *(CRITICAL)*

**Файл:** `Wang....ru.html` строки 462–464, 473–475.

**Корневая причина.** У этих абзацев несколько вариантов попадания в EN:

1. **Окно обработано `leaf_per_segment` / `local_segment_recovery`** —
   то есть cascade уже **знал**, что окно проблемное, упал в пер-сегмент
   и получил EN residual обратно (модель на короткий promt вернула
   исходник). После этого руchemical guard `_apply_post_reassembly_guards`
   **не вызывается повторно**, т.к. пер-сегмент считается terminal путём.
2. **Guard G3 пер-сегментный проход молчит**, т.к. в этом сегменте
   `len > 20` выполняется, но `latin_ratio ≥ 0.8` проверяется в
   **переведённом** выходе — а он идентичен EN-источнику. Если критерий
   реализован как `latin_letters / total_letters`, пробелы/цифры/знаки
   препинания сдвигают отношение, и порог 0.8 не срабатывает на фразах
   с цифрами типа `50 Ω`, `9 dBm`, `35 MHz to 2,7 GHz`.

Проверить grep по логу `logs/translate_wang_full_20260420_191536.log`:
должны быть `[cascade]` строки с `identity_residual` около этих
сегментов. Если их нет — подтверждает gap №2. Если есть, но рядом
`leaf_per_segment` — подтверждает gap №1.

**Фикс P5.5.2 — paragraph-level identity guard.** В
`_apply_post_reassembly_guards` добавить проверку **поверх** сегментов
одного `<p>`:

```python
para_segments = [(i, s, t) for i, s, t in enumerate_segments_of_paragraph(parts)]
if all(t.strip() == s.strip() for _, s, t in para_segments):
    # весь абзац identity — переводим один раз целиком одним вызовом
    joined_src = _join_with_tag_mask([s for _, s, _ in para_segments])
    joined_out = _translate_text_segment(joined_src)
    split_out = _split_with_tag_mask(joined_out)
    if split_out is not None and not _is_identity_residual(...):
        for (idx, _, _), new_t in zip(para_segments, split_out):
            translated_output[idx] = new_t
```

**Фикс P5.5.3 — latin_ratio threshold recalibration.** В утилите
`_is_identity_residual`:

```python
def _is_identity_residual(source, translated, *, target_lang="ru"):
    if target_lang != "ru":
        return False
    src_s = source.strip()
    out_s = translated.strip()
    # exact identity — самый надёжный сигнал
    if src_s and out_s == src_s and _has_latin_words(src_s, min_count=2):
        return True
    # fallback — доля латинских букв среди букв (НЕ символов)
    letters = [c for c in out_s if c.isalpha()]
    if len(letters) < 5:
        return False
    latin = sum(1 for c in letters if "A" <= c.upper() <= "Z")
    return latin / len(letters) >= 0.8
```

Ключ — `letters` вместо всех символов (цифры/пунктуация выкинуты).

**Фикс P5.5.4 — вызов guard после recovery.** В
`_try_batch_translate_with_reason` / `_try_windowed_batch_translate_with_reason`:
**после** того, как `local_segment_recovery` / `leaf_per_segment` вернул
новые строки, **повторно** прогнать `_apply_post_reassembly_guards` по
этим сегментам (один raund). Если guard снова требует recovery — не
зацикливаться, а оставить исходник и записать `reason=identity_terminal`
в лог, чтобы видеть «Gemma отказывается переводить этот абзац»
(возможно, слишком технический контент для 4B-модели).

**Критерий:** оба абзаца (`ADF4351`, `RF amplifier enhances…`) переведены;
`grep -E 'An ADF4351|RF amplifier enhances'` в RU HTML → 0.

### E8. Смешанный EN/RU в одном абзаце  *(HIGH)*

**Файл:** `Wang....ru.html` строка 491 (абзац около Fig. 8).

В одном `<p>` часть сегментов (разделённых inline-тегами
`<a>`/`<sup>`/`<i>`) переведена, часть — идентична EN. Финальный сегмент
`Fig. 8 показывает, как программное обеспечение MATLAB…` — RU.
Предыдущие 6 text nodes того же абзаца — EN.

**Корневая причина.** Пер-сегментный guard G3 применяется к каждому
сегменту изолированно. Когда короткий сегмент (например,
`A half-wave rectifier recovers the envelope amplitude.`) разделён
inline-тегом от соседей, `len > 20` выполняется, но:
- Может не выполняться `≥ 5 слов` (сегмент между `<sup>` и точкой
  может иметь 3–4 слова).
- `latin_ratio` считается по всем символам, цифры/пунктуация смещают.

См. E7 fix P5.5.3 — он также закрывает E8 (пересчёт по буквам, учёт
exact identity). Дополнительно:

**Фикс P5.5.5 — смягчить минимум для identity detection.** В G3:
заменить `len > 20 AND words ≥ 5` на
`len ≥ 10 AND has_latin_word(source) AND (exact identity OR latin_ratio ≥ 0.8)`.
Это ловит короткие сегменты, где 3 слова EN невидимы для текущего
guard'а.

**Критерий:** Абзац Fig. 8 в RU HTML не содержит EN-фраз длиной > 2 слов
кроме терминов (MATLAB, LabVIEW, NI DAQ, dBm — защищены abbreviation
mask, не триггерят identity).

### 8.6.1 Критические файлы (Phase 5.5)

| Файл | Пункт | Что делаем |
|---|---|---|
| `src/zoteropdf2md/translategemma.py` | P5.5.1 | Финальный identity-проход в `translate_html_text_nodes` **после** heading split/join |
| `src/zoteropdf2md/translategemma.py` | P5.5.2 | Paragraph-level identity guard в `_apply_post_reassembly_guards` (группировка сегментов по родительскому `<p>` через `parts`) |
| `src/zoteropdf2md/translategemma.py` | P5.5.3 | Утилита `_is_identity_residual` — letters-only ratio + exact identity + наличие ≥2 латинских слов в source |
| `src/zoteropdf2md/translategemma.py` | P5.5.4 | Повторный проход guard'ов после `local_segment_recovery`/`leaf_per_segment`, с terminal-маркером `identity_terminal` |
| `src/zoteropdf2md/translategemma.py` | P5.5.5 | Смягчённые пороги G3: `len ≥ 10`, убрано `words ≥ 5`, добавлено exact-identity |
| `tests/test_translategemma_html.py` | P5.5.1–P5.5.5 | 4 unit-теста: (a) heading single-inline identity → recovery; (b) whole paragraph identity → single-call paragraph recovery; (c) mixed para-segments identity → per-segment recovery; (d) terminal identity не зацикливается |

### 8.6.2 Зависимости и порядок с Phase 5

- **P5.5 зависит от P5.1 (tag_mask).** Paragraph-level recovery требует
  маскирования `<a>`/`<sup>` внутри объединённого текста, иначе
  восстановление позиций inline-тегов невозможно.
- **Порядок:** сначала коммит P5.1+P5.2 (Phase 5), затем P5.5 отдельным
  коммитом. Тесты E6/E7/E8 пишутся в одном файле с E1/E2 тестами.

### 8.6.3 Верификация Phase 5.5

1. `pytest tests/test_translategemma_html.py -q` — зелёно (включая 4 новых теста).
2. Прогон Wang:
   - `grep -cE '<h[1-6]>\s*<i>\s*[A-Z]\.\s*[A-Z][a-z].*</i>'` в RU HTML → 0 (нет EN-заголовков с `A.`/`B.`/`C.` префиксом).
   - `grep -cE 'An ADF4351|RF amplifier enhances|half-wave rectifier recovers'` в RU HTML → 0.
   - Ручная проверка абзаца у Fig. 8 — RU целиком.
3. Лог: появляются строки `[cascade] reason=identity_terminal` при
   повторном identity после recovery; их общее число ≤ 5% от числа
   translatable segments (иначе — сигнал, что модель систематически
   отказывается от технического контента, нужно усиливать промпт).

---

## 9. Phase 6 — остаточные дефекты (прогон 2026-04-21 08:57)

**Коммиты:** `d75ee12` (P5.1 tag-mask + P5.2 G4), `b90b7e7` (P5.5 identity recovery), `9944629` (P5.3 caption normalize), `1598477` (P5.4 recovery timers), `a0eb730` (cascade→stdout).
**Лог:** `logs/translate_wang_full_20260421_085732.log` (160 LLM-вызовов, `TRANSLATED_SEGMENTS=316`, 1570 s, recovery_calls=100, 303 s).

Несмотря на реализацию P5.1–P5.5, шесть дефектов сохраняются. Ниже — их
точная корневая причина и фикс.

---

### F1. E1 не закрыт — `(...)` перед ссылкой остаётся  *(HIGH)*

**Файл.** Строка 420:
```
…(... <a href="#section-III" class="z2m-section-link">Section III</a>-D)…
```

**Корневая причина.** P5.1 `_apply_tag_mask`/`_restore_tag_mask` **работает**:
`<a href="#section-III">` сохраняется как `@@Z2M_T0@@`, и тег восстанавливается
корректно. Но проблема не в потере тега, а в том, что модель **добавляет `...`
внутри скобок** в переводе: видит `…discussed in (@@Z2M_T0@@Section III@@Z2M_T1@@-D)` и
пишет `…обсуждается в (...@@Z2M_T0@@Section III@@Z2M_T1@@-D)`. После
`_restore_tag_mask` получается `(...<a href>…</a>-D)`. Tag_mask спас тег,
но не предотвратил галлюцинацию `...` **вне** тега.

**Фикс P6.1 — post-restore ellipsis cleanup.** В `_restore_tag_mask` (или
сразу после её вызова в `local_segment_recovery`) добавить regex-замену:

```python
# убрать (... непосредственно перед восстановленным тегом
result = re.sub(r'\(\s*\.{2,}\s*(?=<)', '(', result)
# убрать ... непосредственно после открывающей скобки перед словом
result = re.sub(r'\(\s*\.{2,}\s*', '(', result)
```

Второй паттерн шире — чистит любой случай `(...текст` независимо от тега.

**Файл:** `src/zoteropdf2md/translategemma.py`, функция `_restore_tag_mask`
или caller в `local_segment_recovery`.

**Критерий:** `grep -c '(\.\.\.'` в RU HTML → 0.

---

### F2. E2 не закрыт — trailing `...` остаётся  *(HIGH)*

**Файл.** Строка 424:
```
…Мы определяем...
```
Следующий узел (строка 425): `\(V_{match}\)` — формула (formula_mask).

**Корневая причина.** Guard G4 `trailing_ellipsis_artifact` (P5.2) проверяет
`_has_following_translatable(parts, idx)`. Формульные узлы (`\(…\)`,
обёрнутые через `_apply_formula_mask`) **не считаются translatablе** — они
уже замаскированы и пропускаются бэтчером. Поэтому `_has_following_translatable`
возвращает `False` → guard решает: «предложение закончилось» → не триггерит
recovery. Артефакт `...` остаётся.

**Фикс P6.2 — расширить `_has_following_translatable` на формульные узлы.**
Считать «следующим контентом» не только translatable text nodes, но и
formula-узлы (`kind == "formula"` или содержащие `\(`/`\)` sentinel).
Логика: если после сегмента идёт **любой non-whitespace контент** (текст,
формула, ссылка) — предложение не закончилось и `...` — артефакт.

```python
def _has_following_content(parts, idx):
    for part in parts[idx + 1:]:
        if part.kind == "whitespace":
            continue
        if part.kind in ("text", "formula", "tag"):
            return True
        break
    return False
```

Дополнительно: если recovery возвращает `...` снова — **срезать** trailing
`...` из первого RU результата (`result.rstrip().rstrip('.').rstrip()`) вместо
отката к EN-источнику. Это сохраняет хороший RU-перевод без обрыва.

**Файл:** `src/zoteropdf2md/translategemma.py`, `_has_following_translatable` +
путь recovery в `_apply_post_reassembly_guards`.

**Критерий:** `grep -nE '[а-яёА-ЯЁ]\.\.\.\s*$'` → только строки, где source тоже кончается на `...`.

---

### F3. E6 не закрыт — заголовок `<h2><i>…</i></h2>` остался EN  *(HIGH)*

**Файл.** Строка 468:
```
B. RF Signal Generator and Microcontroller
```

**Корневая причина.** P5.5.1 «финальный identity-проход» в
`translate_html_text_nodes` запускается **до** `_split_heading_text_nodes`
(`translategemma.py:1458-1504`). Heading-merge объединяет узлы, отправляет в
batch, получает перевод, затем split разбивает обратно. Если split возвращает
EN (модель вернула исходник), финальный проход уже прошёл — **узел не
перепроверяется**. Тогда recovery (P5.5.1) никогда не видит этот узел как
проблемный.

Альтернативная гипотеза: проход видит узел, вызывает single-segment recovery,
но модель снова возвращает EN → `identity_terminal` → исходник. Без
`[cascade]` events в логе (F5) проверить невозможно.

**Фикс P6.3 — post-split identity recheck.** После `_split_heading_text_nodes`
добавить явный проход по heading-сегментам:

```python
for idx in heading_segment_indices:
    src = parts[idx].text
    out = translated_output[idx]
    if _is_identity_residual(src, out, target_lang="ru"):
        recovered = _translate_single_with_tag_mask(src)
        if not _is_identity_residual(src, recovered, target_lang="ru"):
            translated_output[idx] = recovered
        else:
            log_terminal(idx, reason="heading_identity_terminal")
```

Место: сразу после блока `_split_heading_text_nodes` в
`translate_html_text_nodes`.

**Файл:** `src/zoteropdf2md/translategemma.py`, `translate_html_text_nodes`
(после строк ~1458–1504).

**Критерий:** `grep -cE '<h[2-6]>\s*<i>\s*[A-Z]\.'` в RU HTML → 0 (нет EN-заголовков с `A./B./C.` префиксом).

---

### F4. E7/E8 не закрыты — абзацы ADF4351 и RF amplifier остались EN  *(HIGH)*

**Файл.** Строки 464–465, 474–475.

**Корневая причина.** P5.5.2 paragraph-level guard и P5.5.3
`_is_identity_residual` реализованы. 100 recovery_calls выполнены. Но
абзацы всё равно EN. Наиболее вероятно — **Gemma-4B capability limit**:
контент высокотехнический (`dsPIC33FJ32GP202`, `divide-by-1/-2/-4/-8/-16/-32/-64`,
`SPI interface`, `VCO`) — модель не умеет переводить это осмысленно и
стабильно возвращает EN. Guard фиксирует `identity_terminal`, откатывается к
EN-источнику (что семантически верно — лучше EN, чем галлюцинированный RU).

Без `[cascade]` событий в логе это подтвердить нельзя — см. F5.

**Фикс P6.4a — prompt augmentation для технического контента.**
При single-segment recovery технического абзаца (определяется по высокой
плотности ASCII-символов: `\w+\d\w+`, аббревиатуры) добавлять в промпт hint:
«Переведи технические термины транслитерацией или оставь как есть, но
переведи всю грамматику и структуру предложения на русский.»

Это не гарантирует результат, но снижает число identity_terminal для
технических абзацев.

**Фикс P6.4b — явная маркировка identity_terminal в выходном HTML.**
Если сегмент сохранён как EN после `identity_terminal` — добавить
атрибут `lang="en"` к его родительскому `<p>`:

```html
<p block-type="Text" lang="en">An ADF4351 chip...</p>
```

Это не исправляет перевод, но позволяет найти «честно EN» сегменты
post-factum и отличить их от bag «guard не сработал».

**Файлы:** `src/zoteropdf2md/translategemma.py` (промпт recovery + `lang=en`
атрибут), `src/zoteropdf2md/single_file_html.py` (CSS-стиль для `[lang="en"]`
— опционально, `color: #c44` или курсив для визуальной диагностики).

---

### F5. `[cascade]` события не попадают в лог  *(CRITICAL — observability)*

**Доказательство.** В `logs/translate_wang_full_20260421_085732.log`:
- `[timer] translategemma.recovery_calls: total=100` — **есть**.
- `[cascade] reason=…` — **0 строк** из 1220 строк лога.
- 100 recovery-вызовов выполнились, но ни один не оставил trace в логе.

**Корневая причина.** Commit `a0eb730` («emit cascade debug lines to stdout
for tee log capture») направил `[cascade]` print в stdout. Но PowerShell
`Tee-Object` перехватывает **stderr** процесса (или только текущего shell),
а Python `print()` по умолчанию идёт в stdout — который уже перехвачен
другим механизмом. Итог: `[cascade]` строки уходят в одну трубу, `[timer]`
строки — в другую, и только `[timer]` попадает в файл.

На практике: всё debug-логирование cascade (reason, window indices, recovery
details) полностью невидимо. Без этого невозможно диагностировать F3/F4
удалённо.

**Фикс P6.5 — единый logging-канал для всех диагностических строк.**
В `translategemma.py` создать или использовать существующий `logging.Logger`
(проверить, есть ли `logger = logging.getLogger(__name__)` в файле):

```python
# Заменить print(f"[cascade] …") на:
logger.info("[cascade] reason=%s window=%s …", reason, window_range)
```

`logger.info` пишет в тот же handler, что и `[timer]` строки → попадает в
файл через Tee. Дополнительно: добавить `flush=True` или явный `sys.stdout.flush()`
после каждой cascade-строки для немедленного сброса в буфер.

**Файл:** `src/zoteropdf2md/translategemma.py` — все `print(f"[cascade]…")`
заменить на `logger.info("[cascade]…")`.

**Критерий:** после следующего прогона Wang:
`grep -c '\[cascade\]'` в логе > 0 и содержит `reason=` строки.

---

### F6. E3 частично закрыт — `TABLE II/III` (all-caps EN prefix) не нормализован  *(MEDIUM)*

**Файл.** Строки 605, 921:
```
TABLE II ПАРАМЕТРЫ ДАТЧИКА
TABLE III Параметры антенны
```

**Корневая причина.** `_normalize_table_caption_style` (commit `9944629`,
`single_file_html.py`) использует regex:

```python
re.compile(r'<p([^>]*)>\s*(Таблица|Table)\s+([IVX]+|\d+)\s*\.?\s*([^<]+?)\s*</p>')
```

Паттерн покрывает `Таблица` (RU) и `Table` (заглавная первая буква EN).
`TABLE` (весь UPPERCASE) **не покрывается** — ни по `Таблица`, ни по `Table`.
При этом в прогоне 19:15 Таблица I/IV были уже переведены к моменту полиша,
а Таблица II/III — нет, они остались с `TABLE` префиксом и выпали из regex.

**Фикс P6.6 — расширить regex на all-caps `TABLE`.**

```python
re.compile(
    r'<p([^>]*)>\s*(TABLE|Таблица|Table)\s+([IVX]+|\d+)\s*\.?\s*([^<]+?)\s*</p>',
    re.IGNORECASE  # ловит TABLE, Table, table, Таблица
)
```

При этом группа 2 заменяется всегда на `Таблица` (RU), независимо от
исходного написания — это финальный полиш, на RU-тексте всегда нужен
`Таблица`.

**Файл:** `src/zoteropdf2md/single_file_html.py`, функция
`_normalize_table_caption_style`.

**Критерий:** `grep -cE '(TABLE|Table)\s+[IVX]+'` в RU HTML → 0;
`grep -cE 'Таблица\s+[IVX]+\.\s+[А-Я]'` → 4.

---

### 9.1 Критические файлы (Phase 6)

| Файл | Пункт | Что меняем |
|---|---|---|
| `src/zoteropdf2md/translategemma.py` | P6.1 | Regex cleanup `(\.\.\.<` → `(<` после `_restore_tag_mask` в `local_segment_recovery` |
| `src/zoteropdf2md/translategemma.py` | P6.2 | `_has_following_translatable` → `_has_following_content`: считать формулы/теги «контентом» |
| `src/zoteropdf2md/translategemma.py` | P6.2 | Fallback при повторном `...`: срезать `...` из первого RU-результата вместо отката к EN |
| `src/zoteropdf2md/translategemma.py` | P6.3 | Post-split identity recheck для heading-сегментов после `_split_heading_text_nodes` |
| `src/zoteropdf2md/translategemma.py` | P6.4a | Расширенный recovery-промпт для технических абзацев (высокая плотность ASCII) |
| `src/zoteropdf2md/translategemma.py` | P6.4b | `lang="en"` атрибут на `<p>` при `identity_terminal` |
| `src/zoteropdf2md/translategemma.py` | P6.5 | Заменить `print(f"[cascade]…")` на `logger.info("[cascade]…")` |
| `src/zoteropdf2md/single_file_html.py` | P6.6 | `re.IGNORECASE` + `TABLE` в regex `_normalize_table_caption_style` |
| `tests/test_translategemma_html.py` | P6.1–P6.4 | 4 unit-теста |
| `tests/test_single_file_html.py` | P6.6 | 1 тест: `TABLE II PARAMS` → `Таблица II. Params.` |

### 9.2 Приоритет и порядок

| Приоритет | Пункт | Обоснование |
|---|---|---|
| P0 | P6.5 (logging) | Без cascade-лога невозможно диагностировать F3/F4. Один коммит, нет рисков. |
| P1 | P6.6 (TABLE regex) | Простой однострочный фикс, закрывает регрессию D2 |
| P1 | P6.1 (ellipsis cleanup) | Однострочный regex, закрывает E1 полностью |
| P2 | P6.2 (trailing ellipsis + formula) | Закрывает E2, требует аккуратного расширения `_has_following_content` |
| P2 | P6.3 (post-split heading recheck) | Закрывает E6, требует знания порядка вызовов |
| P3 | P6.4a (tech prompt) | Вероятностный фикс E7/E8, может не помочь с 4B моделью |
| P3 | P6.4b (`lang="en"`) | Observability, не влияет на перевод |

### 9.3 Верификация Phase 6

1. Прогон Wang после P6.5 (logging):
   - `grep -c '\[cascade\]'` в логе **> 50** (ориентир по 100 recovery_calls).
   - `grep 'identity_terminal'` — видны сегменты, где модель сдалась.
2. После P6.1+P6.6+P6.2+P6.3:
   - `grep -c '(\.\.\.'` → 0.
   - `grep -cE '(TABLE|Table)\s+[IVX]+'` → 0.
   - `grep -cE 'Таблица\s+[IVX]+\.\s+[А-Я]'` → 4.
   - `grep -cE '[а-яёА-ЯЁ]\.\.\.\s*$'` → только строки, где source тоже кончается на `...`.
   - `grep -cE '<h[2-6]>\s*<i>\s*[A-Z]\.'` → 0.
3. `pytest tests/test_translategemma_html.py tests/test_single_file_html.py -q` — зелёно.

---

## 10. Связанные документы

- `docs/TRANSLATION_BATCH_PROTOCOL_PLAN.md` — design id-протокола и Phase 2 cascade.
- `docs/IMPLEMENTATION_PLAN.md` — общий backlog проекта.
- [`docs/UNIVERSALITY_AUDIT.md`](./UNIVERSALITY_AUDIT.md) — аудит универсальности кода, перечень документо-специфичных мест (категории A/B/C/D) и roadmap U1/U2/U3.
- `C:\Users\ELVIS_NIX\.claude\plans\cozy-wiggling-reddy.md` — исторический план (в нём — BUG-A/BUG-B, Commit 1…11 старого бэклога; отдельные пункты (BUG-B, Commit 4, 5, 8) мигрировали сюда как P4.3/P4.4/P4.5; Phase 5 summary-ссылка на этот документ).

---

## 11. Аудит прогона Wang (2026-04-21): непереведённые EN-блоки и план следующей фазы

### 11.1 Наблюдаемый дефект

В итоговом `Wang...ru.html` после успешного полного прогона остаются длинные EN-фрагменты (целиком или смешанные EN/RU внутри одного абзаца), например:

- `in which the sensor's resonant frequency located...`
- `An ADF4351 chip (Analog Devices) is the main component...`
- `...Fig. 9 enlarged photo shows...`
- `The maximum power the telemetry device radiates...`
- `Currently the maximal ADC input from the half wave rectifier...`

Это подтверждено в RU HTML строках ~466, ~475, ~586, ~1156, ~1181.

### 11.2 Подтверждённые причины (по коду)

#### R1. Детектор identity_residual пропускает mixed EN/RU сегменты

Функция `_is_identity_residual` (`src/zoteropdf2md/translategemma.py`) использует жёсткий порог:

- `latin_ratio >= 0.8`

Следствие: если в сегменте уже есть немного RU, но длинный EN-хвост остаётся, такой сегмент может не считаться residual и не уйти в recovery.

#### R2. Paragraph recovery в batch-пути срабатывает только при all(identity)

В `_apply_post_reassembly_guards` для paragraph-group используется условие вида:

- recover group только если `all(_is_identity_residual(...))`

Следствие: частично переведённые абзацы (несколько EN сегментов + несколько RU сегментов) не попадают в paragraph-level recovery и остаются с EN кусками.

#### R3. Terminal-сценарий оставляет EN как «допустимый исход»

После локального retry, если сегмент всё ещё identity, ставится `identity_terminal` и результат сохраняется как есть (`keep_recovered`).

Следствие: при систематическом отказе модели переводить конкретный технический фрагмент EN остаётся в финальном HTML без дальнейшей эскалации контекста.

#### R4. Недостаточная наблюдаемость конкретного прогона

В логе этого прогона виден только агрегатный warning:

- `[WARN] translategemma.en_residual_segments=21`

Но нет cascade-деталей по причинам (в т.ч. из-за запуска без `Z2M_DEBUG_CASCADE=1`), поэтому для расследования пришлось делать пост-фактум анализ сегментов в HTML.

### 11.3 План доработок (Phase 7)

#### P7.1 Расширить детектор EN-residual (ядро)

В `_is_identity_residual` добавить дополнительный критерий «английский run внутри mixed-сегмента»:

- детект непрерывной EN-последовательности (например, >= 12 латинских слов подряд),
- либо `exact_identity` для нормализованного core,
- либо пониженный латинский порог для длинных сегментов.

Цель: не пропускать случаи, где RU есть только локально, а значимый EN-контент остался.

#### P7.2 Перейти с all(identity) на contiguous-runs в абзаце

В `_apply_post_reassembly_guards` заменить стратегию paragraph recovery:

- вместо `all(identity)` искать непрерывные identity-run'ы внутри paragraph-group,
- recovery запускать для каждого run (длина >= 2 сегментов или >= N символов),
- использовать уже существующий wide paragraph path с tag-mask.

Цель: лечить частично переведённые абзацы, не дожидаясь полной identity деградации всей группы.

#### P7.3 Усилить эскалацию после identity_terminal

Если после local retry сегмент всё ещё identity:

- не завершать сразу `keep_recovered`,
- сделать ещё один уровень recovery с расширенным контекстом (paragraph-wide / heading-wide),
- только после неуспеха маркировать как terminal.

Цель: уменьшить финальные EN-residual в production-прогонах.

#### P7.4 Ужесточить recovery prompt для технических EN блоков

В recovery-ветке скорректировать инструкцию:

- разрешать оставлять только аббревиатуры/имена/DOI,
- явно запрещать возврат полного исходного EN-предложения,
- требовать перевод тела фразы.

Цель: снизить частоту «модель вернула исходник без ошибки структуры».

#### P7.5 Расширить file-level telemetry

В финальный summary добавить счётчики:

- `identity_terminal_count`,
- `paragraph_wide_recovery_count`,
- `wide_recovery_split_fail_count`,
- `en_residual_segments` (уже есть).

Цель: видеть причину деградации даже без debug-лога по окнам.

#### P7.6 Тесты на реальные failure-моды

Добавить unit-тесты в `tests/test_translategemma_html.py`:

1. mixed paragraph (часть RU, часть EN): recovery должен сработать для identity-run, не только при all(identity).
2. identity_terminal escalation: после неудачного local retry должен запускаться wide recovery.
3. extended identity detector: длинный EN-run в mixed-сегменте должен считаться residual.
4. non-regression: не трогать чисто RU сегменты и формульные/аббревиатурные узлы.

### 11.4 Критерии приёмки Phase 7

После следующего полного прогона Wang:

1. `en_residual_segments <= 3`.
2. Для проблемных абзацев выше — `grep` по ключевым EN-фразам возвращает 0.
3. В summary-логах видны отдельные счётчики terminal/recovery, а не только общий residual.
4. `pytest tests/test_translategemma_html.py -q` зелёный, плюс полный `pytest -q` без регрессий.

### 11.5 Комментарий по запуску диагностики

Для анализа причин на уровне cascade при debug-прогонах включать:

- `Z2M_DEBUG_CASCADE=1`

и сохранять stdout/stderr в единый лог-файл, чтобы reason-строки не терялись.
