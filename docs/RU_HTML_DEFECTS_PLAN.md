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

### 11.6 Sentinel Escape Defect (Phase 7.3)

Observed in Wang run: unresolved placeholders leaked as escaped sentinels (`@@Z2M\_A0@@ ... @@Z2M\_A10@@`) inside RU HTML.

Root cause:
- model output may escape `_` in sentinel tokens (markdown bias),
- restore regex previously matched only canonical forms (for example `@@Z2M_A0@@`),
- escaped variants were not normalized before restore.

Fix implemented:
- sentinel token patterns for abbrev/tag/formula now accept escaped variants,
- added pre-restore normalization helper (`_normalize_sentinel_escapes`) applied in `_restore_abbrev_mask`, `_restore_tag_mask`, `_restore_formula_mask`,
- added file-level warning counter: `sentinel_leak_segments`.

Acceptance criteria:
- no unresolved `@@Z2M...@@` tokens (canonical or escaped) in final RU HTML,
- warning `sentinel_leak_segments=0` on successful full-file run.

## 12. Phase 8 — аудит прогона 2026-04-21 20:20 (post Phase 7.1/7.2)

### 12.1 Контекст прогона

- Лог: `logs/translate_wang_full_20260421_202010.log` (UTF-16 LE BOM).
- `TRANSLATED_SEGMENTS=324`, `file_total=1803.59 s`,
  `recovery_calls: total=213 time=335.57s avg=1.58s`.
- После коммитов `2a1eda7` (Phase 7.1 robust identity context
  recovery), `dc8042e` (Phase 7.2 resilient identity fallback),
  `8972661` (checkpoint + sentinel-escape fix per §11.6).

### 12.2 Сводка статуса по старым дефектам

| Класс | 08:57 | 20:20 | Статус |
|---|---|---|---|
| `<z2m-…>` marker leak | 0 | 0 | ✅ |
| `@@Z2M…` sentinel leak в HTML | >0 | 0 | ✅ (Phase 7.3) |
| E1 `(...<a>…</a>-D)` | есть | 0 | ✅ |
| E6 EN heading `B. RF Signal…` | есть | 0 | ✅ |
| E7/E8 крупные EN-абзацы | 5 | 0 | ✅ |
| E2 трейлинг `…` на фрагментах | есть | **2** (L417 «Влияние…», L433 «Мы определяем…») | ❌ частично |
| Новый N1 heading-content leak (H2 → префикс абзаца) | — | **1** (L292–295) | ❌ NEW |
| Новый N2 heading-mistranslation | — | **1** (`IV. MEASUREMENT` → `МЕРОПРИЕМ`) | ❌ NEW |
| `[WARN] en_residual_segments` | не лог. | 17 | ⚠️ |

### 12.3 Cascade-гистограмма (248 строк)

| reason | count |
|---|---|
| `identity_terminal` | 56 |
| `identity_context_failed` | 39 |
| `identity_residual` | 38 |
| `structured_parse_failed` | 31 |
| `id_mismatch` | 24 |
| `abbrev_tokens_altered` | 17 |
| `tag_mask_dropped` | 15 |
| `wide_recovery_split_fail` | 14 |
| `formula_tokens_altered` | 5 |
| `identity_residual_paragraph` | 3 |
| `marker_leak` | 2 |
| `trailing_ellipsis_stripped` / `_artifact` | 2/2 |

### 12.4 Подтверждённые новые/недозакрытые дефекты

#### N1 (CRITICAL). Heading `<h2>A. System Model</h2>` потерян, заменён префиксом абзаца

`Wang…ru.html:292–295`:

```html
<h1 id="section-II">II. МЕТОДОЛОГИЯ</h1>
<h2>Пассивный датчик давления, который имплантируется под твердой
мозговой оболочкой, состоит из двух спиральных индукторов. На
рисунке </h2>
<p>Пассивный датчик давления… <a href="#fig-1">Fig. 1</a>…</p>
```

Источник (`.html:292–295`): `<h2>A. System Model</h2>` + `<p>A passive
pressure sensor…</p>`. В выводе heading-контент полностью пропал,
заменён началом следующего абзаца, обрезанным на первом inline-теге
`<a href="#fig-1">`.

**Корневая причина (гипотеза).** Heading-merge path сливает короткий
heading `A. System Model` с префиксом соседнего `<p>` для лучшего
контекста. После LLM-ответа `_split_heading_text_nodes` разрезает
поток по позиции первого inline-тега источника и относит весь prefix
к heading-слоту. Модель в LLM-ответе проигнорировала оригинальный
heading (посчитала его дубликатом открывающего слова абзаца),
поэтому heading-слот получил только paragraph-префикс.

**Фикс P8.7.** В `_split_heading_text_nodes`:

1. Проверять, что heading-slot после split содержит хотя бы частичный
   перевод первого слова оригинального heading (`A. System Model` →
   искать «System Model»/«Модель Системы»/подобное).
2. Если heading-slot длиннее `2 × len(source_heading)` — считать
   split malformed, делать rollback merge (переводить heading и
   paragraph раздельно).
3. Unit-тест: merge heading+paragraph → модель вернула только
   paragraph-перевод → rollback.

**Критерий:** 0 случаев, где первое слово `<hN>` в RU не
соответствует первому слову (или его переводу) source `<hN>`.

#### N2 (HIGH). Heading-mistranslation «MEASUREMENT → МЕРОПРИЕМ»

`Wang…ru.html:737–739`:

```html
<h4 id="section-IV">IV. МЕРОПРИЕМ</h4>
```

Source: `IV. MEASUREMENT`. `МЕРОПРИЕМ` — несуществующее слово;
конфабуляция Gemma-4B на коротком изолированном all-caps heading.

**Корневая причина.** Качественная ошибка LLM, не structural. Короткие
all-caps section-headings — известно нестабильны у Gemma-4B (см. D3
по Wang h1 title).

**Фикс P8.8 — масштабируемая стратегия (без жёсткого словаря).**

Глоссарий покрывает только известные термины и не масштабируется
на новые домены / языки. Используем **многослойную защиту**, все слои
универсальны по контенту и языку.

#### P8.8.a Case-normalization перед LLM (обязательный, дешёвый)

Gemma-4B обучена в основном на normal-case тексте. `IV. MEASUREMENT`
— out-of-distribution → галлюцинации. Преобразовать:

```
IV. MEASUREMENT  →(lower)→  IV. Measurement  →(LLM)→
IV. Измерение     →(re-upper по source-шаблону)→  IV. ИЗМЕРЕНИЕ
```

1. Перед отправкой heading-сегмента в LLM: детектировать all-caps
   слова (ре `\b[A-ZА-ЯЁ]{3,}\b`), привести к Title Case, запомнить
   позиции.
2. После LLM: пройти по translated tokens и восстановить исходный
   регистр для переведённых слов на тех же позициях.
3. Case-preservation идёт и для смешанных паттернов (`II.
   METHODOLOGY` → `II. Методология` → `II. МЕТОДОЛОГИЯ`).

**Файл:** `src/zoteropdf2md/translategemma.py` — новая
`_normalize_heading_case_for_llm` + `_restore_heading_case_after_llm`;
вызовы в heading-translate pipeline.

**Эффект (ожидаемый):** −40…60% heading-галлюцинаций без изменения
модели.

#### P8.8.b Morphological OOV-guard (post-validator)

Слово «МЕРОПРИЕМ» не разбирается ни одним русским морфоанализатором.
Используем **pymorphy3** (OpenCorpora-based, ~15 MB, офлайн) как
post-validator:

1. После LLM-перевода heading — разобрать каждый токен через
   `pymorphy3.MorphAnalyzer().parse(word)`.
2. Токен считается OOV, если у лучшего parse'а
   `score < 0.5` **и** `methods_stack` указывает на
   `UnknAnalyzer` / `UnknSingleAnalyzer`.
3. Если ≥ 1 слов heading'а OOV → запустить retry с
   другим промптом / температурой 0.3 (вместо 0.0) / few-shot (см.
   P8.8.d в справке).
4. Counter: `heading_oov_retry_count`, `heading_oov_unresolved`.

**Файл:** `src/zoteropdf2md/translategemma.py` — новый модуль
`_heading_lexical_validator.py` или функция в том же файле.
Зависимость: `pymorphy3` (добавить в `pyproject.toml`).

**Универсальность:** русско-специфичный. Для target_lang=de/fr/es —
аналоги `hunspell` или `spacy-morph`, но в рамках Phase 8 (RU-фокус)
достаточно pymorphy3. Архитектурно — интерфейс `_HeadingValidator`
с реализациями per-language, подключаемыми через
`TranslateGemmaConfig.target_language_code`.

**Эффект:** близкое к 100% обнаружение конфабуляций, запускает retry
до принятия результата.

#### P8.8.c Dedicated MT-model для heading-сегментов (архитектурный)

LLM — **generative**, дают длинные галлюцинации на коротком
изолированном вводе. Классические MT-модели (NLLB-200,
OPUS-MT-en-ru) — **seq2seq с translation objective**, гораздо
стабильнее на коротких фразах.

1. Router: сегмент, родитель которого — `<hN>`, и длина источника
   ≤ 80 chars → отправить в **NLLB-200-distilled-600M**
   (`facebook/nllb-200-distilled-600M`, ~1.2 GB VRAM,
   `transformers.AutoModelForSeq2SeqLM`).
2. Gemma-4B остаётся для тела.
3. NLLB — 200 языков, переключение `target_language_code` на стороне
   tokenizer'а (`forced_bos_token_id` = `tokenizer.convert_tokens_to_ids(code)`).
4. OOV-guard (P8.8.b) применяется после NLLB тоже — как safety-net.

**Файл:** `src/zoteropdf2md/translategemma.py` — новый путь
`_translate_heading_with_mt`; регистрация модели в
`TranslateGemmaConfig` (`heading_model_name: str | None = None`;
если `None` — fallback на Gemma с P8.8.a+b).

**Универсальность:** лучшая — NLLB покрывает 200 языков и
специально тренирован на short-text translation. Окупается на
любом новом документе/языке.

**Критерии приёмки P8.8 (суммарно):**

- `МЕРОПРИЕМ` и аналогичные несуществующие слова в RU HTML = 0.
- Unit-тесты для all-caps, mixed-case, и edge-case headings
  (`IV.`, `A.`, римские, арабские).
- Counter `heading_oov_unresolved ≤ 1` на Wang.
- Резерв глоссария для top-10 section-keywords (см. список ниже) —
  только как **последняя линия** в цепочке, не как основная стратегия.

*Резервный top-10 глоссарий (последний fallback если P8.8.a+b+c не
дали валидный RU):* `INTRODUCTION → ВВЕДЕНИЕ`, `BACKGROUND → ФОН`,
`METHODOLOGY → МЕТОДОЛОГИЯ`, `METHODS → МЕТОДЫ`, `MEASUREMENT →
ИЗМЕРЕНИЕ`, `RESULTS → РЕЗУЛЬТАТЫ`, `DISCUSSION → ОБСУЖДЕНИЕ`,
`CONCLUSION → ЗАКЛЮЧЕНИЕ`, `APPENDIX → ПРИЛОЖЕНИЕ`, `REFERENCES →
СПИСОК ЛИТЕРАТУРЫ`. Используется **только** при `heading_oov_unresolved`
после всех трёх слоёв. Расширяется через `U1.1` из
`UNIVERSALITY_AUDIT.md`.

---

**Справочно: альтернативные подходы к heading-галлюцинациям** (в
текущую Phase 8 не включены, оставлены для будущих итераций).

- **(ref-A) Few-shot prompting.** Включить 3–5 примеров heading-перевода
  в prompt (любого домена), чтобы модель выучила паттерн в in-context.
  Плюс: адаптивно, не нужно enumerate'ить рубрики. Минус: +prompt
  length, +инференс.
- **(ref-B) Logprob / mean-token-confidence фильтр.** HF `generate(
  return_dict_in_generate=True, output_scores=True)` → средняя
  log-вероятность. На галлюцинациях `mean_logprob < -2.5`. Плюс:
  language-agnostic. Минус: +10–15% инференса, требует scores-доступа.
- **(ref-C) N-best sampling + voting.** `num_return_sequences=3,
  temperature=0.7` → выбор кандидата по: overlap между кандидатами,
  OOV-guard, max logprob. Плюс: устойчивость к единичным срывам.
  Минус: 3× инференс (но только на headings ≤ 10% сегментов).
- **(ref-D) Back-translation validation.** RU-output → EN через ту же
  модель → cosine similarity к source через `sentence-transformers/LaBSE`.
  Галлюцинация даёт низкий cosine. Плюс: семантическая проверка.
  Минус: второй LLM-вызов + embedding-модель.

Любой из ref-A..D может быть подключён поверх P8.8.a+b+c при
необходимости. P8.8 считается закрытым, когда a+b+c сходятся на
критериях приёмки; ref-уровни вводятся только если практика покажет
residual heading-mistranslations > 1 на документ.

**Критерий P8.8 (сводный):** `МЕРОПРИЕМ` / подобных конфабуляций в
RU HTML — 0 на Wang + Teo; `heading_oov_retry_count > 0`
(механизм задействован); `heading_oov_unresolved ≤ 1`.

#### N3 (MEDIUM). Трейлинг `…` на per-segment пути (повтор E2)

`Wang…ru.html:417 "Влияние..."`, `:433 "Мы определяем..."`.

**Корневая причина.** G4 (`_has_trailing_ellipsis_artifact` +
`_strip_unexpected_trailing_ellipsis`) применяется только в
batch/window guards и в final pass, но формуло-дроблёный абзац даёт
короткие single-segment переводы, которые обходят batch. Per-segment
путь не валидирует G4.

**Фикс P8.1.** После успешного single-segment translate вызывать
`_strip_unexpected_trailing_ellipsis` при
`_has_trailing_ellipsis_artifact(...)` с контекстом
`all_source_segments`. Counter: `per_seg_trailing_ellipsis_stripped`.

**Критерий:** `grep -nE '[а-яё]\.\.\.(?!\.|\s*<)' Wang…ru.html` → 0
(кроме случаев, где source тоже `...`).

#### N4 (MEDIUM). `wide_recovery_split_fail=14` — formula-sentinels ломают redistribute

**Evidence:** 14 логов.

**Корневая причина.** `_redistribute_recovered_slice_to_parts`
(`translategemma.py:701–765`) ищет source-parts (`<tag>` и `\(...\)`)
буквальным `.find()` в recovered_chunk. LaTeX-фрагменты могут быть
слегка перефразированы моделью (пробелы, escape-underscores) → split
не сходится → punt.

**Фикс P8.2.** До redistribute:

1. Применять `_apply_formula_mask` к source_slice и работать со
   sentinels `@@Z2MF{n}@@` как разделителями (они не перефразируются).
2. Split по sentinels вместо `\(...\)`.
3. Если и sentinel-based split не сошёлся — fallback на batch-путь на
   segments группы.

**Критерий:** `wide_recovery_split_fail` ≤ 3 на Wang.

#### N5 (HIGH). `identity_terminal=56` — финальная эскалация не развёрнута

**Evidence:** `[WARN] en_residual_segments=17`.

**Фикс P8.4.** После final identity pass:

1. Собрать identity_terminal сегменты по parent-paragraph-group.
2. Для групп с ≥ 2 terminal-сегментов запустить **второй** wide-retry
   с явным промптом («перевести каждое предложение; не копировать
   источник; сохранить математику, ref-tags, `@@Z2M…` как есть»).
3. Промпт параметризуется `target_language_code` (universality —
   связан с U2/U3 в `UNIVERSALITY_AUDIT.md`).
4. При повторном identity — counter `identity_unresolved`.

**Критерий:** `identity_terminal ≤ 15`, `en_residual_segments ≤ 5`.

#### N6 (OBSERVABILITY). Локализовать `en_residual` по сегментам

**Фикс P8.5.** На finalize: для каждого оставшегося identity
сегмента — `[WARN] en_residual seg={seg_no} preview={first 80 chars}`.

### 12.5 Приоритет и порядок работ

1. **P8.3 sentinel regex hardening** (уже в §11.6; проверить, что
   `_normalize_sentinel_escapes` покрывает tag+formula+abbrev).
2. **P8.1 per-segment ellipsis strip** — мелкий точечный фикс.
3. **P8.5 en_residual детализация** — observability, чтобы следующий
   прогон показал точечные seg-id.
4. **P8.7 heading-split rollback** — CRITICAL, без него любой `<h2>`
   с одним-двумя словами рискует быть съеден.
5. **P8.8 heading-glossary** — быстрый win для top-10 научных рубрик.
6. **P8.2 redistribute sentinel-safe split** — уменьшит
   wide_recovery_split_fail.
7. **P8.4 + P8.6 second wide retry + i18n prompt** — closes
   identity_terminal residual; связан с `U2.5` из
   `docs/UNIVERSALITY_AUDIT.md`.

### 12.6 Критерии приёмки Phase 8 (end-to-end)

1. `pytest tests/test_translategemma_html.py tests/test_single_file_html.py -q`
   зелёно (включая новые тесты P8.1/P8.3/P8.4/P8.7/P8.8).
2. `pytest -q` без регрессий.
3. Прогон Wang:
   - `grep -nE '[а-яё]\.\.\.(?!\.|\s*<)' Wang…ru.html` → 0
     (исключая source-ellipsis).
   - `grep -c 'МЕРОПРИЕМ' Wang…ru.html` == 0.
   - `grep -c 'A. System Model\|System Model' Wang…ru.html` ≥ 1
     (heading восстановлен).
   - Cascade: `wide_recovery_split_fail ≤ 3`, `identity_terminal ≤ 15`.
   - `[WARN] en_residual_segments ≤ 5`, `sentinel_leak_segments == 0`.
4. `TRANSLATED_SEGMENTS ≥ 330`.

### 12.7 Связь с `UNIVERSALITY_AUDIT.md`

- P8.6 (target-language-aware recovery prompt) ↔ `U2.5`.
- P8.8 (heading-glossary) ↔ `U1.1` (доменные секции глоссария).
- Все остальные фиксы (P8.1–P8.5, P8.7) — structural, universality-neutral.

### 12.8 P8.8 Regression Postmortem (2026-04-21)

Observed on Wang full translation run (2026-04-21 ~22:24).  
After enabling `P8.8.a+b+c` (case-normalization + pymorphy3 OOV guard + NLLB heading router), translation quality regressed.

Where regression was observed:

1. Acronym transliteration in headings: `LC -> ЛК`.
2. Enumeration label transliteration: `A./B./C.` became Cyrillic (`А./Б./В.`), breaking consistency and references.
3. Weaker technical heading semantics compared to Gemma path:
   - examples included wrong/unnatural forms like `импедантности`, `Резолюция`.
4. Inconsistent heading style in one document (some labels transliterated, some not).
5. `heading_oov_retry_count=0` in run telemetry: OOV guard did not trigger in practice.

Decision taken:

1. Roll back heading MT path from default behavior.
2. Keep heading MT (`NLLB`) as opt-in experimental path only.
3. Disable OOV guard by default (`pymorphy3`) because it did not provide reliable detection on this data.
4. Keep structural hardening in heading recovery (reject candidates containing raw block tags like `<h1>`, `<p>`), since it is quality-neutral and prevents boundary leaks.

Why this decision:

1. Quality and consistency are higher with the previous Gemma-centric heading flow in this domain.
2. NLLB improves some cases but introduces unacceptable acronym/label regressions for production default.
3. pymorphy-based OOV signal is too weak for robust automated retry gating here.

Implementation status:

1. Hotfix commit applied: `c7f639c`
   - `enable_heading_mt: False` by default
   - `enable_heading_oov_guard: False` by default
   - extra heading boundary-leak guard in context recovery
2. Verification after hotfix:
   - `pytest tests/test_translategemma_html.py tests/test_single_file_html.py -q` -> green

Next plan:

1. Rework P8.8 as `experimental, opt-in only`.
2. Production default remains Gemma heading path + existing structural guards.

### 12.9 P9.1 EN-polish for sentence splits around figures (2026-04-22)

Goal: fix a structural source issue where Marker output breaks one English sentence
into two `<p>` blocks with a figure/image block between them, which later hurts
translation agreement.

Scope:

1. Add EN-side polish rule before translation:
   - pattern A: `<p>left without terminal punctuation</p> + <figure|image-only paragraph|Fig-caption paragraph> + <p>right continuation</p>`
   - pattern B: `<p>left ...</p> + <p><img ...></p> + <p>Fig. N ...</p> + <p>right continuation</p>`
   - action: merge `right` into `left`, keep figure/image block in place, remove redundant right paragraph.
2. Keep this conservative:
   - do not merge when left already ends with `. ! ? : ; …`
   - do not merge captions (`Fig. N`, `Figure N`, `Table N`)
   - do not merge when right paragraph has explicit `id=...`
   - do not merge for non-English (Cyrillic detected).

Implementation:

1. Function in `single_file_html.py`:
   - `_repair_sentence_breaks_around_figure_blocks(html) -> (html, count)`
   - node-based scan over adjacent blocks (supports `<p>`, `<figure>`, caption headings, `<table>`),
     with variable-length gap (up to 12 non-prose blocks).
   - table-gap additions: accepts empty/formula-note `<p>` blocks (`\( ... \)`/`\[ ... \]`) as non-prose
     continuations around table sections.
   - includes OCR dehyphenation on merge (`regis-` + `ter` -> `register`) when split is artificial.
   - punctuation-aware merge: no extra space before `,.;:` when right fragment starts with punctuation.
2. Integrated into `polish_html_document()` before section/figure-link normalization.
3. Tests:
   - merge with `<figure>...</figure>`
   - merge with image-only `<p><img ...></p>`
   - merge with caption paragraph `<p>Fig. N ...</p>`
   - merge with image+caption pair between sentence halves
   - no merge when sentence is already finished.

Expected result:

1. Sentence grammar remains coherent in RU output for this class of documents.
2. Figure blocks no longer split one sentence into two translation units.

### 12.10 P9.2 EN-polish for page-break + table/formula intrusions (2026-04-22)

Goal: cover two additional structural break classes that remained after P9.1.

Scope:

1. Page-break split between adjacent paragraphs:
   - pattern: `<p>left without terminal punctuation</p> + <p>right continuation</p>`
   - example: `...wide frequency range` + `(35 MHz to 2.7 GHz), ...`
2. Table intrusion between formula intro and formula itself:
   - pattern: `<p>intro ending with :</p> + <table caption> + <table> + <formula p> (+ follow-up p like "We chose ..."/"where ...")`
   - action: reorder to `intro -> formula -> follow-up -> table caption -> table`.

Implementation:

1. Added `_repair_sentence_breaks_at_page_boundaries(html) -> (html, count)`.
2. Added `_reorder_table_block_away_from_formula_context(html) -> (html, count)`.
3. Integrated both into `polish_html_document()` before figure-gap repair.
4. Added guards:
   - do not merge page-break when right starts like numbered reference (`1.` / `1)`),
   - conservative skip for demonstrative starts (`This/These/It/...`) to avoid paragraph-overmerge.
5. Added support for equation-row wrappers around formulas (`<div class="z2m-equation-row">...`) in reorder detection.

Verification:

1. `tests/test_single_file_html.py`:
   - page-break merge with parenthesized right half,
   - no merge for reference-like next paragraph,
   - table/formula reorder in plain and equation-row wrapped variants,
   - no reorder when formula context is absent.

### 12.11 P9.3 Proposal: Unified score-based split repair (objective alternative)

Status: proposal only, not implemented yet.

Goal: replace multiple narrow heuristics (`figure/table/box/page-break`) with one
objective merge detector that works across any non-prose interruption pattern.

Core idea:

1. For each candidate pair `left <p>` and `right <p>` inside a bounded window,
   compute `merge_score(left, right, context)`.
2. Merge only when score exceeds a conservative threshold and no hard-blocker is hit.
3. Emit explain-logs (`score`, positive/negative features, final decision) for audit.

Feature groups for `merge_score`:

1. Positive continuity signals:
   - left has no terminal punctuation (`. ! ? : ;`),
   - right starts with continuation cues (`lowercase`, `(`, `,`, connector words),
   - dehyphenation cue (`regis-` + `ter`),
   - post-merge text looks less fragmented than pre-merge.
2. Negative separation signals:
   - right resembles new section/reference/list item,
   - right is a standalone sentence with strong sentence-start pattern,
   - context indicates explicit structural boundary.
3. Hard blockers:
   - clear reference numbering / bibliography patterns,
   - explicit anchor/id patterns that must remain independent.

Risks:

1. False merges (merging two truly independent paragraphs).
2. Silent structure degradation (style/author-intended paragraphing erodes).
3. Domain drift (weights tuned on Wang/Teo may fail on different corpora).
4. Harder debugging compared with single-rule behavior.
5. Regression sensitivity when score weights/thresholds are adjusted.

Risk controls:

1. Keep conservative threshold (prefer false-negative over false-positive merge).
2. Preserve hard-blockers and require explicit evidence for merge.
3. Add explain-logs for every accepted merge.
4. Validate on a frozen golden set (Wang, Teo, plus diverse additional docs).
5. Roll out behind a feature flag and compare diff metrics before default enablement.

## 13. Phase 10 — Electrodes Collection (Ahmed / Kaiju / Li), Consolidated Backlog

Context: after user audit of 3 new papers in `md_output/electrodes_W7TECQDX`, часть дефектов уже совпадает с известными классами (split/citation/image), но есть и новые проблемы, которые не были полностью учтены в очереди коммитов.

### 13.1 Подтверждённые дефекты (сводка)

#### Ahmed

EN:

1. Ложные citation-links в не-ссылочном контексте:
   - пример: `(Unit 1)` превращается в `(Unit <sup><a ...>1</a></sup>)`.
2. Артефакты изображений при наличии чистых sidecar-файлов.
3. Пограничные случаи размерностей/единиц около формул и superscript-ссылок (`µm`, степени, unit-exponent).

RU:

1. Частично не переведённые author/citation строки:
   - `Cite this article as: Ahmed, Z., ...` в RU остаётся латиницей.
2. Не переведён `Received: 10 October 2024`.
3. Терминологические и грамматические дефекты (падеж, согласование, редкие доменные термины).
4. Лексическая деградация редких терминов (`steeltrode`, `shank` и пр.).
5. Появление figure-ref варианта там, где в source нет такого ref-линка.
6. Sentinel leaks:
   - `@@Z2M_A0@-Parylene C`
   - `@@Z2M_HSEPДоступность кода`
7. Контент после References не переводится стабильно (Data/Code availability, и т.п.).

#### Kaiju

EN:

1. OCR-caption intrusion: в основной текст попадает крупный кусок подписи (`96ch flexible ...`, `FIGURE 1 | ...`).
2. Нет устойчивой привязки `FIGURE 1 |` к нормальному figure-anchor/figure-block.
3. Артефакты изображений.

RU:

1. Дефект DOI/citation formatting:
   - `Neural Circuits :20. doi: ...`
2. Перевод аббревиатур (`ECoG -> ЭКР/...`) в нежелательных местах.
3. Неестественный перевод длинного caption-like блока.
4. Линки литературы в RU (например, `Table 1`) — требуется отключить генерацию ссылок для RU.
5. Деформация слова `Рисунок` (`рисуног/рисунк/рисуно`) и inconsistency терминов (`Фигура` vs `Рисунок`).

#### Li

EN:

1. Артефакты изображений.
2. Ложные ref-links (`Table 1`, `Fig. 2` contexts).
3. Колонтитулы (`Page X of Y`) протекают в prose.
4. Римские/сносковые суффиксы прилипают к словам (`Foilii`, `Laser ablationiv`, `etchingv`).

RU:

1. Дублирование/смешение сегментов в отдельных абзацах.
2. Тяжёлые искажения перевода таблиц (структурно сложные table cells).
3. Нежелательный перевод материалов/токенов (`TiN -> ТиН`).
4. Структурные table-tail артефакты (`materials`-хвосты, разрывы строк/ячеек).
5. Sentinel leak:
   - `@@Z2M_HSEPКонфликт интересов`

### 13.2 Глобальные корневые причины (C10.x)

1. C10.1 — `_recover_bare_citations()` слишком агрессивен для единиц/служебных токенов.
   - stoplist неполный (`unit`, ряд scientific contexts не покрыт).
2. C10.2 — linkify вызывается и в RU path, хотя в RU это часто ухудшает текст.
3. C10.3 — `_mark_author_line_notranslate()` наивно метит первый `<p>` после `<h1>`, что ломает `Received:` и часть метаданных.
4. C10.4 — `translate_html_text_nodes()` отрезает `references_tail` целиком, не разделяя bibliography и post-reference prose sections.
5. C10.5 — sentinel restore недостаточно устойчив к escaped/искажённым sentinel-вариантам (`@@Z2M_HSEP...`, `@@Z2M_A0@...`).
6. C10.6 — caption/figure нормализация неполная:
   - не все формы caption (`FIGURE N |`) распознаются как caption-node.
7. C10.7 — EN structural repair не покрывает все OCR intrusion классы (header/footer, footnote numeration, roman suffix glue).
8. C10.8 — image inliner не переинлайнивает уже встроенные `data:` URL, даже когда sidecar уже исправлены; из-за этого stale/corrupt embeds сохраняются.
9. C10.9 — отсутствует жёсткая RU-нормализация caption-лексем (`Рисунок`), поэтому в финальном тексте остаются `рисуног/рисунк/рисуно`.
10. C10.10 — нет constrained grammar/terminology harmonization на уровне абзаца; отсюда падежная несогласованность и редкие терминологические промахи.

### 13.3 Очередь коммитов (обновлённая, покрывает все пункты)

#### Commit P10.1 — Citation-link hardening + RU no-link policy (CRITICAL)

Файлы:

1. `src/zoteropdf2md/single_file_html.py`
2. `tests/test_single_file_html.py`

Изменения:

1. Расширить stoplist и контекстные блокеры в `_recover_bare_citations()`:
   - добавить `unit`, `supplementary`, `front`, `doi`, `vol`, `issue`, `page`,
   - блокировать wrapping около scientific unit шаблонов.
2. Разделить политику линковки:
   - EN: сохраняем linkify,
   - RU: выключаем генерацию новых `z2m-ref-link`/`z2m-fig-link` (и при необходимости удаляем auto-generated links post-translation, сохраняя plain text).
3. Тесты:
   - `(Unit 1)` не становится citation-link,
   - DOI/`11:20` не ломаются,
   - RU path не генерирует новые citation-links.

#### Commit P10.2 — Metadata translation targeting (Received/Authorship) (HIGH)

Файлы:

1. `src/zoteropdf2md/translategemma.py`
2. `tests/test_translategemma_html.py`

Изменения:

1. Заменить heuristic в `_mark_author_line_notranslate()`:
   - детектировать действительно author-line, а не “первый `<p>` после `<h1>`”.
2. Не маркировать `Received:` как `translate="no"`.
3. Тесты:
   - `Received: ...` переводится,
   - author names остаются корректными (без порчи имён), но metadata-строки не “замораживаются” ошибочно.

#### Commit P10.3 — References-tail split policy (HIGH)

Файлы:

1. `src/zoteropdf2md/translategemma.py`
2. `tests/test_translategemma_html.py`

Изменения:

1. Отказ от “вырезать весь хвост после References”.
2. Разделить:
   - bibliography list (не переводим),
   - post-reference narrative sections (`Data availability`, `Code availability`, `Conflict of interest`, `Funding`, `Acknowledgements`) — переводим.
3. Тесты на смешанный хвост: bibliography остаётся, пост-секции переводятся.

#### Commit P10.4 — Sentinel hygiene hardening (CRITICAL)

Файлы:

1. `src/zoteropdf2md/translategemma.py`
2. `src/zoteropdf2md/single_file_html.py`
3. `tests/test_translategemma_html.py`

Изменения:

1. Унифицировать `_normalize_sentinel_escapes()` перед restore (A/T/F/HSEP).
2. Дополнить tolerant-restore для поломанных хвостов (`@@Z2M_A0@` и близкие варианты).
3. Финальный post-restore guard:
   - если `@@Z2M_` остался → warning counter `sentinel_leak_segments`.
4. Тесты:
   - escaped sentinel и частично испорченный sentinel корректно восстанавливаются.

#### Commit P10.5 — Caption lexeme normalization (`Рисунок`) (HIGH)

Файлы:

1. `src/zoteropdf2md/single_file_html.py`
2. `tests/test_single_file_html.py`

Изменения:

1. RU caption normalizer для figure captions:
   - `FIGURE/Fig/Фиг/Фигура/Рис/...` + OCR-деформации (`рисуног/рисунк/рисуно`) -> каноническое `Рисунок N. ...`.
2. Применять только в caption-context (не global replace по документу).
3. Тесты:
   - набор дефектных вариантов нормализуется в один канон.

#### Commit P10.6 — EN structural anti-intrusion pack (HIGH)

Файлы:

1. `src/zoteropdf2md/single_file_html.py`
2. `tests/test_single_file_html.py`

Изменения:

1. Расширить non-prose detection:
   - page headers/footers (`Page X of Y`, journal footer lines),
   - long affiliation-footnote blocks.
2. Нормализатор римских/footnote suffix glue (`Foilii`, `ablationiv`, `etchingv`) в EN pre-translation path.
3. Распознавание caption формата `FIGURE N |` как figure-caption node (anchor/link compatible).
4. Тесты на Kaiju/Li snippets.

#### Commit P10.7 — Image refresh from sidecar (CRITICAL)

Файлы:

1. `src/zoteropdf2md/single_file_html.py`
2. `src/zoteropdf2md/pipeline.py` (если нужно для флага/telemetry)
3. `tests/test_single_file_html.py`

Изменения:

1. Добавить режим принудительного реинлайна локальных изображений:
   - если есть sidecar image, обновлять `<img src="data:...">` на sidecar bytes.
2. Не менять remote/http/img-placeholder случаи.
3. Telemetry:
   - `inline_images_replaced`, `inline_images_skipped`, `inline_sidecar_mismatch_count`.
4. Тесты:
   - stale data-URI заменяется на sidecar.

#### Commit P10.8 — RU quality layer: terminology + grammar harmonization (MEDIUM/HIGH)

Файлы:

1. `src/zoteropdf2md/translategemma.py`
2. `src/zoteropdf2md/abbreviations.py` (или новый glossary config)
3. `tests/test_translategemma_html.py`

Изменения:

1. Domain glossary (materials/biomed/EE):
   - `steeltrode`, `shank`, `ECoG`, `TiN` и др.
2. Constrained harmonization pass:
   - правит только терминологию/грамматику,
   - не трогает числа, формулы, теги, ссылки.
3. Приоритет: paragraph-wide recovery > single leaf для сложных table/caption фрагментов.
4. Тесты:
   - падежная согласованность в контрольных предложениях,
   - `TiN` не транслитеруется в `ТиН`.

### 13.4 Критерии приёмки Phase 10

1. Sentinel leaks:
   - `grep -R \"@@Z2M_\" md_output/electrodes_W7TECQDX/*.ru.html` -> 0.
2. RU no-link policy:
   - новые `class=\"z2m-ref-link\"`/`z2m-fig-link` в RU не добавляются auto-linking шагом.
3. Ahmed:
   - `Received:` переводится,
   - нет `(Unit <sup>...)` ложного citation-wrap.
4. Kaiju:
   - `FIGURE 1 |` корректно распознаётся как caption block,
   - `Neural Circuits :20` дефект устранён.
5. Li:
   - `Page X of Y` не разрывает prose,
   - `Foilii/ablationiv/etchingv` очищены.
6. RU stylistics:
   - `рисуног/рисунк/рисуно` -> 0,
   - согласование/термины в контрольных фрагментах улучшены без structural regression.
7. Regression safety:
   - `pytest tests/test_single_file_html.py tests/test_translategemma_html.py -q` зелёно,
   - повторный smoke-run на Wang/Teo без ухудшений.

### 13.5 Универсальность и риск-контроль

1. Все risky-правила language-gated и context-gated (caption/table/footer only), чтобы не портить общий prose.
2. Потенциально спорные правила (`P10.8` harmonization) вводятся feature-flag-ом и проходят gold-set сравнение до default-on.
3. Image-refresh (`P10.7`) делает замену только по локальному sidecar mapping, без внешних URL.
