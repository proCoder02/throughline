"""Writes ml/finetune_kaggle.ipynb (step 3 + 4). Kept as code so the notebook
can be regenerated and reviewed in diffs.  python ml/build_notebook.py"""
import json
import os

md = lambda s: {"cell_type": "markdown", "metadata": {}, "source": s}
code = lambda s: {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s}

cells = [
    md("# Throughline: fine-tune Qwen2.5-3B (free Kaggle GPU)\n\n"
       "1. **Settings** (right panel): Accelerator **GPU T4 x2**, Internet **On**.\n"
       "2. **Add data**: upload `train.jsonl` and `test.jsonl` from `ml/data/` as a **private** dataset named `throughline-train`.\n"
       "3. **Run all**. About 30-60 minutes. The last cell writes `throughline-3b-Q4_K_M.gguf` to the Output tab; download it.\n\n"
       "The score cell runs twice: on the plain model first, then after training. If the plain model already scores well, you may not need the fine-tune at all."),
    code("!pip install -q unsloth\n"),
    code("import json, glob, re\n"
         "D = glob.glob('/kaggle/input/*/train.jsonl')[0].rsplit('/', 1)[0]\n"
         "train = [json.loads(l) for l in open(f'{D}/train.jsonl')]\n"
         "test = [json.loads(l) for l in open(f'{D}/test.jsonl')][:60]\n"
         "print(len(train), 'train,', len(test), 'test')\n"),
    code("from unsloth import FastLanguageModel\n"
         "model, tok = FastLanguageModel.from_pretrained('unsloth/Qwen2.5-3B-Instruct', max_seq_length=4096, load_in_4bit=True)\n"
         "model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=16, lora_dropout=0, target_modules=[\n"
         "    'q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'], use_gradient_checkpointing='unsloth')\n"),
    code("# Score: valid JSON, and the same answer as the big teacher model on held-back examples.\n"
         "def js(t):\n"
         "    m = re.search(r'\\{.*\\}', t or '', re.S)\n"
         "    try: return json.loads(m.group(0)) if m else None\n"
         "    except ValueError: return None\n"
         "def same(task, a, b):\n"
         "    if task == 'extract': return len(a.get('commitments') or []) == len(b.get('commitments') or []) and len(a.get('decisions') or []) == len(b.get('decisions') or [])\n"
         "    if task == 'evidence': return bool(a.get('done')) == bool(b.get('done'))\n"
         "    if task == 'radar': return set(a.get('ids') or []) == set(b.get('ids') or [])\n"
         "    return all(k in a for k in ('agreed', 'open', 'options'))\n"
         "def score(label):\n"
         "    FastLanguageModel.for_inference(model)\n"
         "    res = {}\n"
         "    for ex in test:\n"
         "        prompt = tok.apply_chat_template(ex['messages'][:-1], tokenize=False, add_generation_prompt=True)\n"
         "        ids = tok(prompt, return_tensors='pt').to('cuda')\n"
         "        out = tok.decode(model.generate(**ids, max_new_tokens=600, do_sample=False)[0][ids['input_ids'].shape[1]:], skip_special_tokens=True)\n"
         "        got, want = js(out), js(ex['messages'][-1]['content'])\n"
         "        r = res.setdefault(ex['task'], [0, 0, 0]); r[0] += 1; r[1] += got is not None; r[2] += bool(got and same(ex['task'], got, want))\n"
         "    for t, (n, ok, agree) in res.items(): print(f'{label:7} {t:14} valid JSON {ok/n:5.0%}   agrees with teacher {agree/n:5.0%}   (n={n})')\n"
         "    FastLanguageModel.for_training(model)\n"
         "score('before')\n"),
    code("from datasets import Dataset\n"
         "from trl import SFTTrainer, SFTConfig\n"
         "ds = Dataset.from_list([{'text': tok.apply_chat_template(e['messages'], tokenize=False)} for e in train])\n"
         "trainer = SFTTrainer(model=model, tokenizer=tok, train_dataset=ds, args=SFTConfig(\n"
         "    dataset_text_field='text', max_seq_length=4096, per_device_train_batch_size=2, gradient_accumulation_steps=4,\n"
         "    num_train_epochs=2, learning_rate=2e-4, warmup_steps=5, logging_steps=10, optim='adamw_8bit',\n"
         "    fp16=True, output_dir='out', report_to='none', seed=1))\n"
         "trainer.train()\n"),
    code("score('after')\n"),
    code("# Ship only if 'after' shows valid JSON >= 95% and agreement >= 85% (see ml/README.md, step 6).\n"
         "model.save_pretrained_gguf('throughline-3b', tok, quantization_method='q4_k_m')\n"
         "import os; print([f for f in os.listdir('throughline-3b') if f.endswith('.gguf')])\n"),
]
nb = {"cells": cells, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}, "language_info": {"name": "python"}},
      "nbformat": 4, "nbformat_minor": 5}
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finetune_kaggle.ipynb")
json.dump(nb, open(path, "w", encoding="utf-8"), indent=1)
print("wrote", path)
