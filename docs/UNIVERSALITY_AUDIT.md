# Аудит универсальности кода ZoteroPDF_2_MD

> **Дата аудита:** 2026-04-21.
> **Контекст:** после Phase 6 (`docs/RU_HTML_DEFECTS_PLAN.md` §9) встал
> вопрос — универсальны ли фиксы P5/P5.5/P6 для произвольного
> документа, или часть заточена под Wang 2017 LC Sensor / Teo 2024 GAI?
> Аудит проведён параллельным проходом трёх Explore-агентов по
> `src/zoteropdf2md/translategemma.py`, `src/zoteropdf2md/single_file_html.py`,
> `src/zoteropdf2md/pipeline.py`, `src/zoteropdf2md/abbreviations.py`,
> `run_headless.py`.
>
> **Назначение документа:** зафиксировать все найденные проблемы
> универсальности (файл, строка, риск, предложенный фикс) и дать
> трёхстадийный roadmap: **U1** — блокеры для второго документа,
> **U2** — i18n-ready без breaking changes, **U3** — полная
> конфигурируемость.

---

## 1. Область покрытия аудита

| Модуль | LOC | Фокус |
|---|---|---|
| `translategemma.py` | 2671 | Gemma-4B → RU cascade, id-протокол, guards, heading merge, sentinels |
| `single_file_html.py` | ~1300 | polish HTML: caption/figure/table anchors, link autolinking, LaTeX fixups, abbreviation restore |
| `pipeline.py` | ~100 | orchestration: inline_images, translate_html_file, polish |
| `abbreviations.py` | ~80 | hardcoded глоссарий аббревиатур EN→RU |
| `run_headless.py` | ~70 | пример CLI с hardcoded коллекциями Zotero |

Все остальные модули (`attachments`, `export_modes`, `gui`, `history`,
`llm_bundle`, `marker_runner`, `models`, `naming`, `output_state`,
`paths`, `runtime_temp`, `staging`, `webdav_*`, `zotero*`) относятся к
pipeline-инфраструктуре и ортогональны языку/домену контента — в аудит
не входят.

---

## 2. Категории риска

Каждая находка классифицирована по риску на неподдержанных документах:

- **CRITICAL** — ломает pipeline или выдаёт заведомо бесполезный
  результат на любом документе, не попадающем в узкое подмножество
  «англ. electronics/medical paper → RU».
- **HIGH** — регрессирует качество на 50–70% документов вне core,
  при этом pipeline не падает (silent degradation).
- **MEDIUM** — условно универсально; пороги/whitelist'ы нуждаются в
  перекалибровке при смене модели или расширении языковых пар.
- **OK** — полностью универсально, не зависит от языка/домена/модели.

---

## 3. Findings

### 3.A. CRITICAL — блокеры для любого документа кроме Wang/Teo

#### A1. Hardcoded коллекции Zotero в `run_headless.py`

**Файл:** `run_headless.py:40–50`.

**Что не универсально:** коллекции `LSZKA7Z9` («intracranial») и
`ETB2AQMX` («llm_medicine») + пути `md_output/intracranial/` и
`md_output/llm_medicine/` вписаны прямо в код. Для другого пользователя
Zotero это неработоспособно — нужно править `.py`.

**Фикс (U1.4).** Перенести список в `config/headless_runs.yaml`:

```yaml
runs:
  - collection_key: LSZKA7Z9
    output_subdir: intracranial
    include_subcollections: true
  - collection_key: ETB2AQMX
    output_subdir: llm_medicine
    include_subcollections: true
```

Добавить CLI-флаги `--collection-key`, `--output-subdir`,
`--config <path>`. `run_headless.py` читает yaml, yaml-пример
пользователь правит сам.

---

#### A2. Hardcoded глоссарий аббревиатур в `abbreviations.py`

**Файл:** `abbreviations.py:19–76` (модуль-уровневый dict
`_LATIN_ABBREV_TO_RU` и `LATIN_ABBREV_TO_RU`).

**Что не универсально:** глоссарий содержит термины только из двух
доменов — electronics/RF (LC, VNA, ICP, SNR, ADC, MEMS, FPGA, RF, MIMO)
и medical AI (GAI, RAG, RLHF, RLAIF, LLM, GPT, NLP, FDA). На physics/
bio/law-статьях нет ни одного термина из словаря → модель переводит
всё, включая технические аббревиатуры, которые должны остаться в
оригинале (например: `SEM` в physics, `PCR` в biology, `SCOTUS` в law).

**Фикс (U1.1).** Вынести в `config/glossary.default.yaml` с секциями по
доменам:

```yaml
electronics:
  LC: LC        # identity — don't translate
  VNA: VNA
  MEMS: МЭМС    # transliteration if desired
medical:
  PCR: ПЦР
  MRI: МРТ
  CT: КТ
cs_ai:
  LLM: LLM
  RAG: RAG
law:
  SCOTUS: SCOTUS
```

Добавить CLI-флаг `--glossary-file <path>` для merge с пользовательским
глоссарием. `abbreviations.py` превращается в loader вместо
hardcoded dict.

---

#### A3. Wang-специфичная heuristic «датчик → датчика»

**Файлы:**
- `translategemma.py:~72–73` — `_HEADING_ACRONYM_SENSOR_PATTERN`,
  regex: `(<(i|em|b|strong)\b[^>]*>\s*[A-Z0-9]{2,8}\s*</\2>)\s+датчик\b`.
- `translategemma.py:~1241–1243` — применение в
  `_fix_heading_translation_breaks`:
  `lambda m: f"{m.group(1)}-датчика"`.

**Что не универсально:** паттерн срабатывает **только** на слове
`датчик` после inline-аббревиатуры. Был добавлен для закрытия Wang D3
(`<i>LC</i> датчик → LC-датчика`). На медицинской статье с
`<i>MRI</i> сканер` / `<i>CT</i> изображение` / `<i>EEG</i> запись`
guard не срабатывает → заголовок остаётся с неправильной
морфологией.

**Фикс (U1.2).** Обобщить на top-50 русских слов в научных заголовках:
датчик, сенсор, прибор, метод, модель, система, сигнал, сканер,
изображение, запись, спектр, анализ, диагноз, классификатор, алгоритм,
сеть, слой, регрессия и т. д. Вынести в
`config/acronym_suffix_morphology.yaml`:

```yaml
nominative_to_genitive:
  датчик: -датчика
  сенсор: -сенсора
  сканер: -сканера
  метод: -метода
  сигнал: -сигнала
  система: -системы
```

Переписать regex как generic:
```regex
(<(i|em|b|strong)\b[^>]*>\s*[A-Z0-9]{2,8}\s*</\2>)\s+([а-яё]+)\b
```
и lookup морфологии по group(3).

---

#### A4. Wang-специфичный комментарий-пример в коде

**Файл:** `translategemma.py:~2204–2206` — блок комментария:

```
# This fixes the issue where "Sensor" inside <h1>...<i>LC</i>Sensor</h1> was
# translated separately and incorrectly as "Датчик" (nominative) instead of
# "датчика" (genitive).
```

**Что не универсально:** не поломка pipeline, но документирует
Wang-специфику как «эталонный случай», что вводит в заблуждение при
дальнейшей поддержке.

**Фикс (U1.3).** Переписать как обобщённое «heading with inline
acronym tag followed by a translatable noun».

---

### 3.B. HIGH — частично универсально, регресс на 70%+ документов вне core

#### B1. `_FIG_CAPTION_PARA_PATTERN` — узкий набор нумерации

**Файл:** `single_file_html.py:118`.

**Текущий regex:**
```regex
(<p\b[^>]*)>([ \t\r\n]*(?:Fig|Рис|рис|Фиг|фиг|FIG)\.?\s*(\d+)\.)
```

**Что не ловит:**
- `Figure 3.` (полное слово — многие journals требуют)
- `Fig. 3a.`, `Рис. 1S.`, `Fig. 2(b).` (supplementary/подписи c литерой)
- `Figure 3.1.` (вложенная нумерация)
- `图3.` (китайский)
- `Abb. 3.` (немецкий — Abbildung)

**Фикс (U1.5).** Расширить:
```regex
(<p\b[^>]*)>\s*(?:Figure|Fig|Рис|Фиг|Abb|图)\.?\s*(\d+(?:\.\d+)*[a-zA-Zа-я]?)
```

---

#### B2. `_TABLE_CAPTION_PARA_PATTERN` — смешанный регистр и нумерация

**Файл:** `single_file_html.py:129`.

**Текущий regex:**
```regex
(<p\b[^>]*>\s*)(?:TABLE|Таблица)\s+([IVXLCM\d]+)\s*[\.\-:]?\s*([^<]*?)(\s*</p>)
```

**Что не ловит:**
- `Table 1` (title case — это регресс F6 Phase 6)
- `Tbl.`, `Tbl 3`
- `Table S1` (supplementary)
- `Table 1.1` (вложенная нумерация)
- `Table 1a` (с литерой)

**Фикс (U1.5).** Расширить и добавить `re.IGNORECASE`:
```regex
(<p\b[^>]*>\s*)(?:TABLE|Tbl|Таблица|Табл|Tabelle)\.?\s+(S?\d+(?:\.\d+)*[a-zа-я]?|[IVXLCM]+)\s*[\.\-:]?\s*([^<]*?)(\s*</p>)
```

---

#### B3. `_URL_TRAILING_CONNECTOR_RE` — только английские коннекторы

**Файл:** `single_file_html.py:143–144`.

**Текущий regex:**
```regex
^(.*/)(?:and|or|the|to|in|of|for|with|from|at|by|a|an)$
```

**Что не ловит:** RU-тексты (`example.ru/и`), DE (`und`, `oder`), FR
(`et`, `ou`, `de`, `à`).

**Фикс (U2.3).** Вынести в `config/connector_words.yaml` со словарём по
языкам; подгружать set'ы для source+target.

---

#### B4. `_fix_heading_translation_breaks` — только кириллица

**Файл:** `single_file_html.py:1214–1249`.

**Текущая логика:** после `</i>` понижает регистр следующей
`[А-ЯЁ]` буквы.

**Что не универсально:** при target_lang=en или de заглавная
остаётся неизменной → тот же баг, что D3 для Wang, но «в другую
сторону». Также regex жёстко `[А-ЯЁ]`.

**Фикс (U2.2).** Сделать target-lang-aware:
- `ru` → `[А-ЯЁ]`
- `en` → `[A-Z]`
- `de` → `[A-ZÄÖÜ]`

Target-lang прокинут из `TranslateGemmaConfig.target_language_code`.

---

#### B5. `_normalize_table_caption_style` — всегда `Таблица N.`

**Файл:** `single_file_html.py:1257–1275`.

**Текущее поведение:** независимо от target_lang выдаёт `Таблица N. …`.

**Что не универсально:** при target_lang=de нужно `Tabelle N.`,
при en — `Table N.`, при zh — `表 N.`.

**Фикс (U2.1).** Target-lang-aware словарь
`config/target_lang_captions.yaml`:

```yaml
ru:
  table: Таблица
  figure: Рис.
de:
  table: Tabelle
  figure: Abb.
en:
  table: Table
  figure: Fig.
zh:
  table: 表
  figure: 图
```

То же для `_add_figure_anchors`.

---

#### B6. `_TABLE_CAPTION_ALLCAPS_PATTERN` — римские цифры + UPPERCASE

**Файл:** `translategemma.py:~424`.

**Текущий regex:**
```regex
^\s*TABLE\s+([IVX]+)\s+([A-Z0-9][A-Z0-9\s,()/:+\-]{3,})\s*$
```

**Что не покрывает:** `TABLE 1`, `TABLE S1`, `Table 3`, mixed-case
варианты.

**Фикс (U1.5).** Объединить с B2 regex + добавить IGNORECASE.

---

### 3.C. MEDIUM — условно универсально, требует перекалибровки

#### C1. `latin_ratio >= 0.8` в `_is_identity_residual`

**Файл:** `translategemma.py:~966`.

**Что не универсально:** порог 0.8 откалиброван под EN→RU, где RU —
кириллица, и любая «много-латиница» сигнализирует непереведённый
EN-кусок. Для EN→DE / EN→FR / EN→ES (все target — латиница) этот
порог сработает **всегда** → guard ложно-положительный → бесконечный
retry loop.

**Фикс (U2.5).** Сделать target-lang-aware:
- target_lang ∈ {ru, uk, be, zh, ja, ar, he, th, …} (non-Latin script)
  → старый порог 0.8.
- target_lang ∈ {de, fr, es, it, pt, …} (Latin script) → не использовать
  `latin_ratio`. Вместо этого — exact identity + embedding similarity
  к source (или просто exact-identity как единственный сигнал).

---

#### C2. `_has_min_latin_words(source, min_count=2)`

**Файл:** `translategemma.py:~954`.

**Что не универсально:** короткие технические строки (например, одна
длинная составная аббревиатура типа `RT-PCR` или заголовок из одного
слова `METHODOLOGY`) не триггерят guard.

**Фикс (U2.5).** Сделать target-lang-aware и добавить альтернативную
ветку для коротких строк: если `len(source) < 50 and source == result`
exact → identity residual.

---

#### C3. Heading merge условие `len(text_indices) < 2`

**Файл:** `translategemma.py:~2098`.

**Что не универсально:** `<hN>` с одним inline-тегом (например,
`<h2><i>B. RF Signal Generator</i></h2>` — вся строка в одном text
node) не подпадает под merge. Идёт на обычный batch-путь и при неудаче
модели возвращается EN (E6 Phase 5.5 → P6.3).

**Фикс (U2.x — частично покрывается P6.3).** Heading с любым текстом
должен попадать на специальный путь recovery с расширенным контекстом
(предыдущий/следующий абзац), а не только при ≥2 узлов.

---

#### C4. Hardcoded chunk/batch constants

**Файл:** `translategemma.py:149, 419, 422, 2659, 2668`.

**Константы:**
- `max_chunk_chars: int = 1800`
- `_MAX_BATCH_CHARS = 80_000`
- `_MAX_WINDOW_BATCH_CHARS = 40_000`
- `context_margin = 4096`
- `max_new_tokens = min(8192, dynamic_cap)`

**Что не универсально:** откалиброваны под Gemma-4B с 8K context.
При смене модели (Gemma-7B, Qwen3-14B, локальный Llama 3) нужна
перекалибровка.

**Фикс (U3.1).** Вынести в `TranslateGemmaConfig` с вычислением
дефолтов от `tokenizer.model_max_length` и числа токенов на символ
(языко-зависимое).

---

#### C5. `_BARE_CITATION_*_PATTERN` — `\d{1,3}`

**Файл:** `single_file_html.py:91–102`.

**Что не ловит:** ссылки с номером >999 (meta-analyses бывают с ~1500
источников), диапазоны `10–15`, `10—15` (en-dash, em-dash).

**Фикс (U3.2).** Расширить до `\d{1,4}` и добавить диапазоны
`\d+[-–—]\d+`.

---

#### C6. `_SECTION_REF_PATTERN` — только римские

**Файл:** `single_file_html.py:111–113`.

**Regex:**
```regex
\b(Section|Раздел[еауо]?)\s+([IVX]{1,6})\b
```

**Что не ловит:** `Section 3.2` (арабские + вложенные — чаще в
современных статьях), `Section A`, `Section 12`.

**Фикс (U3.3).** Расширить:
```regex
\b(Section|Раздел[еауо]?)\s+([IVX]+|\d+(?:\.\d+)*|[A-Z])\b
```

---

#### C7. `_SUBSCRIPT_SPILL_RE` — whitelist LaTeX команд

**Файл:** `single_file_html.py:167–170`.

**Что не покрывает:** `\mathbb`, `\mathcal`, `\mathfrak`,
user-defined `\mycommand`.

**Фикс (U3.4).** Вынести whitelist в
`config/latex_commands.yaml` + опциональный regex `\\\\[A-Za-z]+`
для любых LaTeX-команд.

---

#### C8. `_REFERENCES_HEADING_PATTERN` — узкий whitelist названий

**Файл:** `single_file_html.py:29–30`.

**Не покрывает:** `Cited References` (IEEE), `Works Cited` (MLA),
`Ссылки на источники`, `Referencias` (ES), `Citazioni` (IT),
`Referenzen` (DE — уже есть, но `Literaturverzeichnis` нет).

**Фикс (U2.4).** Расширить словарь + вынести в
`config/references_heading_words.yaml` с секциями по языкам.

---

### 3.D. OK — полностью универсальные

- **ASCII sentinels** `@@Z2M_A{n}@@`, `@@Z2M_T{n}@@`, `@@Z2MF{n}@@`,
  `@@Z2M_HSEP@@`, `<z2m-i{n}/>`, `<z2m-sep/>`, `<z2m-end/>` —
  структурные, языко-нейтральные.
- **HTML-parsing regex** `_TAG_SPLIT_PATTERN`, `_OPEN_TAG_PATTERN`,
  `_CLOSE_TAG_PATTERN`, `_TRANSLATABLE_TEXT_PATTERN` (последний
  покрывает Latin + Cyrillic + CJK — универсален для всех поддержанных
  target-lang).
- **Cascade guards** G1 (marker_leak), G2 (duplicate_leak), G4
  (trailing_ellipsis) — структурные, не зависят от языка. G3
  (identity_residual) — с оговоркой C1.
- **Byte-token artifact patterns** `<0xHH>` — специфика SentencePiece,
  не домена.
- **MathJax injection**, **base64 image inlining**, **CSS default
  styles**, **UTF-8 charset injection**.
- **Paragraph-level identity guard** (P5.5.2), **final identity pass**
  (P5.5.1), **contiguous-run recovery** (P6.4) — архитектурные,
  универсальные.
- **Marker runner**, **Zotero attachment fetcher**, **staging/naming/
  output_state** — domain-ortho.

---

## 4. Roadmap — 3 стадии

### 4.U1. Блокеры для второго документа (P0)

Минимум, необходимый, чтобы pipeline работал на произвольной статье
из intracranial/llm_medicine с качеством не хуже Wang.

| Пункт | Файл | Суть |
|---|---|---|
| **U1.1** | `abbreviations.py` + `config/glossary.default.yaml` | Вынести глоссарий в yaml с доменными секциями. Loader + CLI `--glossary-file` |
| **U1.2** | `translategemma.py:72, 1241` + `config/acronym_suffix_morphology.yaml` | Generic morphology для top-50 RU-слов, не только «датчик» |
| **U1.3** | `translategemma.py:2204` | Переписать комментарий в обобщённую форму |
| **U1.4** | `run_headless.py` + `config/headless_runs.yaml` | Yaml-конфиг вместо hardcoded коллекций |
| **U1.5** | `single_file_html.py:118, 129, 143, 29` + `translategemma.py:424` | Расширить regex caption'ов на `Figure N`, `Fig. Na/S1`, `Table N` mixed case, арабские/римские нумерации, supplementary |

### 4.U2. i18n-ready без breaking changes (P1)

Добавить target-lang awareness, чтобы EN→DE, EN→FR, EN→ZH работали
без регрессов.

| Пункт | Файл | Суть |
|---|---|---|
| **U2.1** | `single_file_html.py:1257` + `config/target_lang_captions.yaml` | Caption-слова по target-lang (Таблица/Tabelle/Table/表) |
| **U2.2** | `single_file_html.py:1214` | `_fix_heading_translation_breaks` target-lang-aware regex |
| **U2.3** | `single_file_html.py:143` + `config/connector_words.yaml` | URL-коннекторы по языкам |
| **U2.4** | `single_file_html.py:29` + `config/references_heading_words.yaml` | Расширить whitelist названий |
| **U2.5** | `translategemma.py:966, 954` | `latin_ratio` и `min_count` target-lang-aware (Latin-script target → exact-identity вместо ratio) |

### 4.U3. Полная конфигурируемость (P2)

Долгий трек — параметризация всех численных порогов и whitelist'ов
через конфиг, поддержка произвольных моделей.

| Пункт | Файл | Суть |
|---|---|---|
| **U3.1** | `translategemma.py:149, 419, 422, 2659, 2668` | Chunk/batch constants в `TranslateGemmaConfig` с авто-вычислением от `tokenizer.model_max_length` |
| **U3.2** | `single_file_html.py:91–102` | `_BARE_CITATION_*` — `\d{1,4}` + диапазоны `\d+[-–—]\d+` |
| **U3.3** | `single_file_html.py:111–113` | `_SECTION_REF_PATTERN` поддержать арабские, буквенные |
| **U3.4** | `single_file_html.py:167–170` + `config/latex_commands.yaml` | LaTeX whitelist в конфиг |
| **U3.5** | `pipeline.py:57` + `gui.py` | `translation_target_language_code` — обязательный параметр CLI/GUI без hidden дефолта |

---

## 5. Матрица «документ × нужные исправления»

| Документ (гипотетический) | U1 | U2 | U3 | Комментарий |
|---|---|---|---|---|
| Wang 2017 LC Sensor (EN→RU) | ✅ ok | n/a | n/a | Baseline, работает (после Phase 6) |
| Teo 2024 GAI medicine (EN→RU) | ✅ ok | n/a | n/a | Работает (glossary покрывает GAI/RAG/LLM) |
| Хирургическая статья RCT (EN→RU) | **нужен U1.1 + U1.2** | n/a | n/a | Глоссарий без PCR/MRI/OR/ICU → термины переведены; `<i>CT</i> сканер` — морфология неправильная |
| Физика частиц (EN→RU) | **нужен U1.1** | n/a | n/a | Нет SEM/TEM/XRD/QCD в глоссарии |
| Юридический обзор (EN→RU) | **нужен U1.1** | n/a | n/a | Нет SCOTUS/GDPR/HIPAA |
| Medical paper (EN→DE) | **U1.1** | **нужен U2.1, U2.2, U2.5** | n/a | `Tabelle N.` вместо `Таблица N.`; `_fix_heading_translation_breaks` не работает; `latin_ratio≥0.8` ложно-положительный (DE — латиница) → бесконечный retry loop |
| Physics paper (EN→ZH) | **U1.1** | **нужен U2.1** (`表`/`图`) | n/a | Caption style; остальное работает |
| Law review (EN→ES) | **U1.1** | **U2.1, U2.2, U2.3, U2.5** | n/a | Всё то же, что DE, + URL-коннекторы |
| Meta-analysis с 1500+ ссылок (EN→RU) | **U1.5** | n/a | **U3.2** | `\d{1,3}` не покрывает номера ≥1000 |
| Документ на Qwen3-14B вместо Gemma-4B | ok | ok | **нужен U3.1** | Chunk constants откалиброваны под 8K context — на 32K не используют full capacity |

**Выводы матрицы:**
- **U1 достаточно** для любой EN→RU научной статьи в новом домене.
- **U2 необходим** для любого EN→X, где X — латиница (DE, FR, ES, IT).
- **U3 — долгий трек**, не блокирует расширение в 90% сценариев.

---

## 6. Приоритет и порядок реализации

1. **U1 целиком** (5 коммитов) — 1 спринт, разблокирует переход на
   любой EN→RU документ.
2. **U2.1 + U2.2** — 1 коммит, открывает EN→DE, EN→FR для caption и
   headings.
3. **U2.3 + U2.4** — 1 коммит, URL-коннекторы и bibliography headings.
4. **U2.5** — 1 коммит, target-lang-aware identity guards (снимает
   блокер EN→DE/FR/ES/IT).
5. **U3** — по необходимости, не в текущем планировании.

---

## 7. Связанные документы

- [`docs/RU_HTML_DEFECTS_PLAN.md`](./RU_HTML_DEFECTS_PLAN.md) — основной
  backlog дефектов Wang RU HTML (D1–F6).
- [`docs/TRANSLATION_BATCH_PROTOCOL_PLAN.md`](./TRANSLATION_BATCH_PROTOCOL_PLAN.md)
  — id-протокол, cascade.
- [`docs/IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) — общий
  backlog проекта.
