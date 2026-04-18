# Translation Batch Protocol Hardening Plan

**Last updated:** 2026-04-18
**Scope:** `src/zoteropdf2md/translategemma.py`
**Status:** Design (not yet implemented)

---

## 1. Проблема: позиционная хрупкость `<z2m-sep/>`

### 1.1 Что происходит сегодня

Batch-перевод собирает N текстовых сегментов в одну строку через
`<z2m-sep/>` разделитель и шлёт модели за один вызов:

```python
# translategemma.py:338
_BATCH_SEPARATOR = "\n<z2m-sep/>\n"

# translategemma.py:528
batch_text = _BATCH_SEPARATOR.join(masked_segs)
translated_batch = translate_text(batch_text)
translated_parts = _BATCH_SEP_PATTERN.split(translated_batch)

# translategemma.py:545
if len(translated_parts) != len(segments):
    return None, f"separator_mismatch expected={len(segments)} got={len(translated_parts)}"
```

Если модель дропнула или дублировала **хотя бы один** `<z2m-sep/>`,
количество кусков не совпадёт — весь window отбрасывается, и
`_try_windowed_batch_translate_with_reason` возвращает `None`, что в
`translate_html_text_nodes` ведёт к **полному fallback на per-segment**:

```
[12:57:17] TranslateGemma progress: 306/306 segments (100%)
[12:57:17] TranslateGemma failed: ... separator_mismatch expected=10 got=9
           → весь документ переведён в fallback (306 отдельных LLM-вызовов)
```

### 1.2 Почему это не просто «медленнее»

1. **Качество перевода падает**: per-segment режим переводит каждый
   узел в изоляции. Глоссарий, тон, стилистика расплываются по
   статье, падежи и согласования рвутся (эффект аналогичен BUG-B
   «Sensor → Датчик»).
2. **x10–x40 медленнее**: 306 вызовов вместо 30–40 batch-окон.
3. **Больше рисков галлюцинации**: prompt-leak, meta-commentary,
   повторения — всё чаще встречаются на коротких single-segment
   вызовах.

### 1.3 Почему модель теряет разделители

* `<z2m-sep/>` — это **одна** уникальная последовательность токенов.
  Если модель «решила» вставить перевод фразы через запятую / тире,
  она скорее склеит два сегмента, чем напишет `\n<z2m-sep/>\n`.
* Positional decode означает: **любая единичная ошибка ломает весь
  window целиком**. Нет частичного recovery.
* При overlap=1 и window=8 окно получает 9–10 сегментов; чем больше
  сегментов, тем выше вероятность потери хотя бы одного разделителя.

---

## 2. Что находится «в опасной зоне»? → **Ничего, кроме plain-text**

Критичный архитектурный факт, который снимает большинство опасений:

### 2.1 Как работает текущий split HTML

```python
# translategemma.py:32
_TAG_SPLIT_PATTERN = re.compile(r"(<[^>]+>)")

# translategemma.py:36
_SKIP_TRANSLATION_TAGS = {
    "script", "style", "code", "pre", "math", "svg", "a", "sup", "sub"
}

# translategemma.py:1001
parts = _TAG_SPLIT_PATTERN.split(html)
```

`parts[]` после split — **чередование** текстовых узлов и HTML-тегов:

```
[text, "<h1>", text, "<i>", text, "</i>", text, "</h1>", text, "<sup>", ...]
```

В batch-перевод попадают **только text-узлы**, и только те, что **не
внутри** `_SKIP_TRANSLATION_TAGS`.

### 2.2 Что это значит на практике

| Элемент | Попадает в batch? | Перевод может сломать? |
|---------|-------------------|------------------------|
| `<sup><a href="#ref-12">12</a></sup>` | ❌ нет, это теги | ❌ невозможно |
| `<a href="https://example.com">text</a>` | ❌ нет, `a` в skip-list | ❌ невозможно |
| Атрибуты `id="ref-42"`, `href="#ref-42"` | ❌ нет, внутри открывающего тега | ❌ невозможно |
| `<img src="data:image/..." alt="...">` | ❌ нет, атрибуты | ❌ невозможно |
| `<figure>`, `<math>`, `<code>` | ❌ нет, skip-list или структура | ❌ невозможно |
| Plain-text «as shown in Figure 2» | ✅ да | ⚠️ да, **только смысл текста** |

Другими словами: **смена batch-протокола не может потерять ссылки, URL,
якоря, id или атрибуты**. Эти элементы физически не доходят до
модели — они живут в `parts[i]` с чётной индексацией (теги) и
пересобираются обратно через `"".join(parts)` нетронутыми.

### 2.3 Citation linking работает ПОСЛЕ перевода, на собранном HTML

`_add_reference_ids_and_citation_links()`, `_link_paren_ref_citations()`,
`_autolink_plain_urls()` и т.п. вызываются в `polish_html_document()`
поверх уже собранного (переведённого) HTML. Они работают как на EN,
так и на RU и не зависят от того, как batch-протокол
организован — лишь бы результат был валидным HTML с сохранённой
структурой.

**Вывод:** любые изменения в batch-протоколе безопасны по отношению к
ссылкам, URL, атрибутам и якорям библиографии.

---

## 3. Цели улучшения

1. Убрать **позиционную** fragility: потеря одного маркера не должна
   ронять весь window.
2. **Сохранить context-window** как можно дольше: не скатываться в
   per-segment при первой же проблеме.
3. **Явная валидация** структуры вывода (coverage, uniqueness, formula
   placeholders).
4. **Reason-rich diagnostics** для каждого уровня fallback — чтобы
   видеть, какие кейсы доминируют в реальных прогонах.

---

## 4. Решение

### 4.1 Замена протокола: id-addressed XML-marker

**Старый формат** (positional):
```
Paragraph one text.
<z2m-sep/>
Paragraph two text.
<z2m-sep/>
Paragraph three text.
```

**Новый формат** (id-addressed, self-closing XML-tag):
```
<z2m-i1/>Paragraph one text.
<z2m-i2/>Paragraph two text.
<z2m-i3/>Paragraph three text.
```

Parsing на выходе — `re.findall(r'<z2m-i(\d+)/>([\s\S]*?)(?=<z2m-i\d+/>|\Z)', out)`.

### 4.2 Почему именно этот формат

| Вариант | Плюсы | Минусы |
|---------|-------|--------|
| `[1] text [2] text` | компактно | конфликт с bracket-citations `[5]` в теле статей |
| `### 1\ntext\n### 2\ntext` | markdown-natural | модель может вставить свои `###` при переводе |
| `{"1": "...", "2": "..."}` JSON | строгий формат | LLM часто ломает JSON (кавычки в тексте) |
| **`<z2m-i1/>text`** | ✅ совпадает с уже знакомым моделью `<z2m-sep/>`; ✅ нулевая вероятность коллизии с обычным научным текстом; ✅ однозначный regex | токены — ~3–4 штуки на маркер |
| `<!--1-->text` | нейтральный HTML | модель может удалить как «мусор» |

**Выбор: `<z2m-i{n}/>`** как прямое расширение текущего именования.
Модель уже видела `<z2m-sep/>` и `<z2m-p id=…/>` (prompt-leak mask) —
новый токен того же семейства встраивается естественно.

### 4.3 Преимущества перед `<z2m-sep/>`

1. **Потеря одного маркера ≠ потеря всех.** Если модель дропнет
   `<z2m-i5/>`, сегменты 1–4 и 6–N парсятся корректно. Потерян
   только один сегмент — его можно восстановить локально.
2. **Duplicate detection.** Если модель повторила `<z2m-i3/>` дважды —
   это тоже детектируется (uniqueness check), а не молча смешивает
   содержимое.
3. **Out-of-range detection.** Модель могла придумать `<z2m-i99/>` при
   N=10 — отловится как `id_out_of_range`.
4. **Coverage check.** Считаем множество id в выводе; если `set !=
   expected` — точно знаем, какие сегменты пропали.

---

## 5. Cascade восстановления (4 уровня)

Порядок попыток для каждого window:

### Level 1: Lenient recovery (без LLM вызова)

До того, как считать window неудачным:

* **Off-by-one match.** Если `len(parsed) == N-1` и все найденные id
  составляют непрерывный диапазон `[1..k-1, k+1..N]` — считаем что
  модель дропнула один маркер. Вставляем оригинальный (непереведённый)
  текст для пропущенного id как fallback и логируем
  `lenient_recovery_level=off_by_one_missing id=k`.
* **Split heuristic** (опционально): если удалось найти (N-1) маркеров,
  но между двумя соседними id сидит текст длиной примерно равной
  сумме оригинальных длин двух сегментов — можно попробовать
  heuristic split по длине (менее надёжно, логируем отдельно).

Stopping rule: lenient recovery допускается только при потере **≤
10%** сегментов окна. Иначе сразу переходим к Level 2.

### Level 2: Retry (один повтор того же окна)

Если парсер упал или lenient не применим:

* Повторяем вызов `translate_text(batch_text)` **один раз** с теми же
  параметрами.
* Логируем `retry_attempt=1 reason=<original_reason>`.
* Если retry тоже провалился — Level 3.

### Level 3: Bisect (рекурсивное разделение core-окна)

* Разбиваем `core_segments` пополам: `[0:N/2]` и `[N/2:N]`.
* Каждая половина переводится с own overlap (overlap сегментов из
  соседних половин).
* Рекурсивный вход в cascade (Level 1–3) с меньшим окном.
* **Invariant:** bisect **не режет merged heading nodes** (см. §7).
* Минимальный размер окна после bisect — 2 сегмента. Ниже — Level 4.

### Level 4: Per-segment fallback (последний уровень)

Существующий `_translate_text_segment()` для каждого оставшегося
сегмента. Логируется с метрикой сколько сегментов в итоге оказалось
на этом уровне: `final_fallback_segments=N`.

### Сводная схема

```
translate_window(segments)
  ↓
[L1] parse id-markers + lenient recovery (off-by-one)
  ↓  success? → return
[L2] retry_once
  ↓  success? → return
[L3] bisect into halves → recursive translate_window
  ↓  all halves succeeded? → return
[L4] per-segment fallback for unrecoverable tail
```

---

## 6. Валидация parse-результата

После парсинга id-blocks выполняются три проверки **до** возврата
результата:

| Проверка | Условие провала | Reason-код |
|----------|-----------------|------------|
| Structured parse | `re.findall(...)` вернул 0 matches | `structured_parse_failed` |
| Coverage | `set(found_ids) != set(expected_ids)` | `id_mismatch missing=... extra=...` |
| Uniqueness | `len(found_ids) != len(set(found_ids))` | `duplicate_ids ids=...` |
| Placeholder integrity | для каждого сегмента: формулы из `_apply_formula_mask` сохранены в переводе один-к-одному | `placeholder_mismatch seg=k expected=X got=Y` |

При любом провале — cascade переходит к следующему уровню.

**Placeholder integrity** особенно важна: если модель «перевела»
`__FORMULA_MASK_7__` как `__ФОРМУЛА_МАСКА_7__` или вставила её не
туда — мы этого хотим дождаться, а не молча выводить криво.

---

## 7. Invariants и корректность взаимодействия с существующим кодом

### 7.1 Heading merge (BUG-B, commits 6af0ddb/93057f2)

`_merge_heading_text_nodes()` объединяет несколько text-nodes внутри
одного `<h1>-<h6>` в **один сегмент** через Unicode PUA separator
`\uE001`. Для batch-протокола это **прозрачно**: merged node идёт
как обычный сегмент с одним id.

**Invariant 1:** `bisect` **не должен** разрывать merged heading
nodes. В `_merge_heading_text_nodes()` уже возвращается `heading_merges`
dict — использовать его, чтобы пометить merged indices как
«bisect-atomic». При разбиении core — соблюдать границы atomic-групп.

### 7.2 Overlap context

Текущая схема `overlap_segments=1` добавляет 1 сегмент слева и
справа для context. В id-протоколе overlap реализуется так же:
batch-окно состоит из `[ext_start:ext_end]`, id=1 присваивается
первому сегменту в extended, результаты overlap-сегментов
**отбрасываются** при сборке финального перевода (берутся только
core segments).

**Invariant 2:** id нумерация в batch стабильна и соответствует
positional индексу в `segments[ext_start:ext_end]`. Overlap
сегменты получают id=1 и id=N (крайние), core — всё что между.

### 7.3 Formula masks

`_apply_formula_mask()` заменяет LaTeX/math на токены
`__Z2M_FM_N__`. Эти токены должны пройти через модель неизменёнными.

**Invariant 3:** Placeholder integrity check (§6) применяется **до**
`_restore_formula_mask()`. Если проверка не прошла — cascade
переходит на следующий уровень, а не пытается восстановить
полуразрушенные формулы.

### 7.4 Prompt-leak mask

`_apply_prompt_leak_mask()` заменяет опасные строки (списки
аббревиатур) на токены `<z2m-p id="N"/>`. Аналогично formulas —
эти токены должны пройти через модель и восстановиться после.

**Invariant 4:** id-протокол batch (`<z2m-iN/>`) и prompt-leak
(`<z2m-p id="N"/>`) используют **разные** regex-namespace — коллизии
невозможны. Парсинг id-блоков использует строго `<z2m-i(\d+)/>` (без
атрибутов, без whitespace), prompt-leak — `<z2m-p id="(\d+)"/>`
(с атрибутом).

### 7.5 Existing polish steps (citation linking, URL auto-link)

`polish_html_document()` работает **после** batch/fallback, на уже
собранном HTML. Изменение batch-протокола не влияет на работу
`_add_reference_ids_and_citation_links`, `_autolink_plain_urls`,
`_link_paren_ref_citations` и прочих — они видят только финальный
HTML.

**Гарантия:** ни одна ссылка, URL, attr, id-якорь **не может
пострадать** от изменений в batch-протоколе. Весь линкинг
ответственен за себя, независимо от способа перевода.

---

## 8. Telemetry и диагностика

Каждый батч-перевод лога события с уровнями:

```
batch_ok           window=[12:20) ids=8/8 formulas=3/3
retry_success      window=[12:20) reason=separator_mismatch level=retry
lenient_recovery   window=[12:20) reason=off_by_one_missing missing=[5] level=lenient
bisect             window=[12:20) split=[12:16)+[16:20) level=bisect_depth=1
final_fallback     window=[12:20) segments=8 level=per_segment
```

В summary-логе файла — агрегат:

```
TranslateGemma summary:
  total_windows=42 total_segments=306
  L1_lenient=3  L2_retry_success=5  L3_bisect=2  L4_per_segment=0
  recovery_rate=100%  per_segment_fallback_rate=0%
```

Это позволяет в будущих прогонах видеть, **какие уровни cascade
срабатывают чаще всего** и решать, нужны ли дальнейшие улучшения
(например, увеличить retry до 2, добавить temperature=0 на retry,
и т.д.).

---

## 9. Non-Goals (в этом патче)

1. **Document-level glossary harmonization.** Отдельная задача —
   собрать глоссарий терминов из первых N сегментов документа и
   передавать в prompt как справочник. Планируется как follow-up.
2. **JSON-only response protocol.** JSON с LLM — фрагмильная идея
   (кавычки в тексте, экранирование, trailing commas). id-based XML
   tags дают почти ту же строгость при большей устойчивости.
3. **Per-document temperature tuning.** Сейчас temperature фиксирована;
   подбор temperature per retry — отдельный эксперимент.

---

## 10. Implementation plan (поэтапно)

### Phase 1 — id-protocol core
1. Добавить `_BATCH_ID_MARKER_FORMAT = "<z2m-i{n}/>"`.
2. Переписать `_try_batch_translate_with_reason`:
   * сборка: `"\n".join(f"{marker(i+1)}{seg}" for i, seg in enumerate(masked_segs))`;
   * parse: `_ID_BLOCK_PATTERN = re.compile(r'<z2m-i(\d+)/>([\s\S]*?)(?=<z2m-i\d+/>|\Z)')`;
   * валидация coverage + uniqueness.
3. Старые константы `_BATCH_SEPARATOR` и `_BATCH_SEP_PATTERN` — удалить.
4. Обновить prompt (если нужна явная инструкция «сохраняй маркеры
   `<z2m-iN/>`»).

### Phase 2 — cascade
1. Расширить `_try_windowed_batch_translate_with_reason`:
   * L1 lenient recovery (off-by-one).
   * L2 retry_once.
   * L3 bisect (рекурсия с min_window=2).
2. Добавить atomic-group check: не резать merged heading nodes.

### Phase 3 — validation
1. Placeholder integrity check (formulas).
2. Reason-rich logging (см. §8).

### Phase 4 — tests
Unit-тесты в `tests/test_translategemma_html.py`:

* `test_id_protocol_decode_success` — прямой случай, все id на месте.
* `test_id_protocol_off_by_one_lenient` — дропнутый маркер восстанавливается из оригинала.
* `test_id_protocol_duplicate_ids_triggers_retry` — duplicate → L2.
* `test_id_protocol_bisect_recovery` — L2 падает, L3 success на половинах.
* `test_id_protocol_placeholder_mismatch` — формула потерялась → fallback.
* `test_id_protocol_preserves_heading_merge` — merged heading node не разбивается bisect.
* Regression test: citation `<sup><a href="#ref-N">` не меняется после смены протокола.

---

## 11. Acceptance criteria

1. ✅ Существующие тесты проходят.
2. ✅ Новые тесты (см. §10 Phase 4) зелёные.
3. ✅ На реальных прогонах (Wang + Teo):
   * `per_segment_fallback_rate ≤ 5%` (было ~100% в последнем прогоне Teo).
   * Все internal references (`href="#ref-N"`) работают.
   * Все external URLs (`<a href="https://...">`) работают.
   * Figure anchors (`href="#fig-N"`) работают.
4. ✅ Telemetry в логе чётко показывает, какой уровень cascade
   сработал на каждом window.
5. ✅ Никаких регрессий по BUG-A/BUG-B (ref-number single, heading
   переводится корректно).

---

## 12. Ответ на вопрос «не потеряем ли ссылки?»

**Короткий ответ: нет, принципиально не потеряем.**

**Обоснование:**

1. `_SKIP_TRANSLATION_TAGS` исключает `a`, `sup`, `sub` и их
   содержимое из batch. Теги и атрибуты физически **не попадают** в
   модель — они живут в `parts[]` между текстовыми сегментами и
   собираются нетронутыми через `"".join(parts)`.
2. Изменение batch-протокола касается **только формата** передачи
   plain-text узлов модели. Структура HTML, теги `<a>`, атрибуты
   `href`, `id` не трогаются этим изменением.
3. Citation linking (`_add_reference_ids_and_citation_links`, …) и URL
   autolink работают **после** перевода, на уже собранном HTML. Они
   независимы от batch-протокола.
4. Heading merge (BUG-B) использует separator `\uE001` **внутри**
   одного сегмента — это orthogonal уровень к batch-протоколу и не
   конфликтует с новым id-маркером.

Единственный риск — **качество перевода самого текста**, но он как
раз улучшается за счёт резкого снижения per-segment fallback (с
~100% для Teo до ≤5%).
