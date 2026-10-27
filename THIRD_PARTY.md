# Third-party components and data

This repository (schema, Lua scripts, tools, tests, the Weasel patch and the
settings app) is licensed under the GNU General Public License v3 (`LICENSE`).
The projects below are used by it. "In git" says whether any of their files are
committed here; everything else is downloaded or generated locally (see
`.gitignore`, `tools/fetch_models.py`, `tools/fetch_assist.py`) and keeps its
own licence.

This project is not affiliated with ASUS. It contains no ASUS code or
dictionary data; "ASUS Smart Input" (華碩智慧輸入法) is named only to describe
the behaviour it imitates. The schema id `asus_zhuyin` and the `asus_*` file
names are kept for compatibility with existing learned data.

## Engine and front end

| Component | Licence | Use | In git |
|---|---|---|---|
| [librime](https://github.com/rime/librime) | BSD-3-Clause | input-method engine (via Weasel / DaoHui) | no |
| [librime-lua](https://github.com/hchunhui/librime-lua) | BSD-3-Clause | runs `rime/lua/*.lua` | no |
| [librime-octagram](https://github.com/lotem/librime-octagram) | GPL-3.0 | language-model plugin | no |
| [Weasel 小狼毫](https://github.com/rime/weasel) | GPL-3.0 | Windows front end; `patches/weasel/` is a patch to its source (GPL-3.0) | patch only |
| [rime-bopomofo](https://github.com/rime/rime-bopomofo), [rime-terra-pinyin](https://github.com/rime/rime-terra-pinyin), [rime-essay](https://github.com/rime/rime-essay) | LGPL-3.0 | `bopomofo_tw` schema, `terra_pinyin` dictionary and `essay.txt` (shipped with Weasel; imported at run time, read by the tools) | no |
| [Tauri](https://tauri.app) and the crates in `app/daohui-settings/src-tauri/Cargo.lock` | MIT / Apache-2.0 (per crate) | settings app | lock file only |

## Data

| Data | Licence | Use | In git |
|---|---|---|---|
| [McBopomofo 小麥注音](https://github.com/openvanilla/McBopomofo) `phrase.occ`, `BPMFMappings.txt` (`Source/Data`, commit e663357dfdfa) | MIT (BPMFMappings originally from libtabe `tsi.src`, BSD) | Taiwan phrase frequencies: `terra_pinyin.mcb`, next-word and segmentation tables; `third_party/mcbopomofo/` | no |
| [SymSpell](https://github.com/wolfgarbe/SymSpell) `frequency_dictionary_en_82_765.txt`, `frequency_bigramdictionary_en_243_342.txt` | MIT (made from Google Books Ngram, CC BY 3.0, and SCOWL) | English word ranks and follower words; `third_party/symspell/` | no |
| [easy_en (rime-easy-en)](https://github.com/BlindingDark/rime-easy-en) `easy_en.dict.yaml` | LGPL-3.0 (its word list credits ECDICT, MIT) | English lexicon for mixed input and suggestions (`tools/build_lang_data.py`) | no |
| wanxiang-lts-zh-hant.gram ([RIME-LMDG](https://github.com/amzxyz/RIME-LMDG), amzxyz) | CC-BY-4.0 | language model (`rime/grammar.yaml`); downloaded by `tools/fetch_models.py` or the settings app | no |
| zh-hant-t-essay-bgw.gram ([rime-octagram-data](https://github.com/lotem/rime-octagram-data)) | see upstream | older language model, optional | no |
| Taiwan Ministry of Education dictionaries (國語辭典簡編本、重編本、成語典) | CC BY-ND 3.0 TW: no derivatives may be distributed | local only: `rime/terra_pinyin.tw.dict.yaml` and the readings used to make evaluation key sequences; never committed or packaged (`tools/package_daohui_data.py --check` enforces it) | no |
| [OpenCC](https://github.com/BYVoid/OpenCC) 1.1.9 | Apache-2.0 | `opencc_dict` builds the `rime/opencc/*.ocd2` tables (built from source in `third_party/`) | no |

## Branding

| Component | Licence | Use | In git |
|---|---|---|---|
| [Yuji Syuku](https://github.com/Kinutafontfactory/Yuji), © 2021 The Yuji Project Authors | SIL Open Font License 1.1 (`branding/fonts/OFL.txt`) | the brush 島 of the DaoHui icons | yes (`branding/fonts/`) |
| Microsoft JhengHei Bold (ships with Windows) | Microsoft font licence | rendered into the 16 and 20 px frames of three `.ico` files; the font itself is not included (see `branding/README.md` for an all-OFL alternative) | rendered pixels only |

## Writing helper (`assist/`) and homophone experiments

| Component | Licence | Use | In git |
|---|---|---|---|
| [Harper](https://github.com/Automattic/harper) harper-cli v2.12.0 | Apache-2.0 | grammar checker | no |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) b11559 | MIT (the CUDA runtime DLLs in its Windows build are NVIDIA's) | local model server | no |
| Qwen3.5-4B GGUF (Qwen, converted by unsloth) | Apache-2.0 | writing-helper model | no |
| Llama-3.2-Taiwan-3B GGUF (lianghsun, quantized by QuantFactory) | Llama 3.2 Community License ("Built with Llama") | homophone-correction experiment (`tools/eval_homophone.py`) | no |

## Ideas and test fixtures

| Project | Licence | Use | In git |
|---|---|---|---|
| [BlendKey](https://github.com/0rigind1865-bit/BlendKey) | MIT | ideas (feature comparison, grammar-restricted homophone correction); its `Tests/Fixtures/sentences.tsv` is a local held-out evaluation set in `third_party/blendkey/` | no |
