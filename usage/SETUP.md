# Setup

Environment and usage notes for this fork. Validated on Debian 13 under WSL2,
CPU-only, 2026-10-02.

Upstream ships no dependency manifest ("Requirements: same as Uni-Mol"), so
`environment.yml` and this file reconstruct one.

**Contents**

- [Linux on Windows (WSL2)](#linux-on-windows-wsl2)
- [Environment](#environment)
- [Uni-Core](#uni-core)
- [Model weights](#model-weights)
- [Running a screen](#running-a-screen)
- [Azure ML](AZURE.md) - running on a cloud instance
- [GPU](#gpu)
- [Fpocket (optional)](#fpocket-optional)
- [Known traps](#known-traps)

Native Windows does not work: Uni-Core is built for Linux, and the bundled
HomoAug helpers are Linux ELF binaries. Use WSL2, a Linux VM, or a cluster.

---

## Linux on Windows (WSL2)

Skip on native Linux.

```powershell
wsl --install Debian --no-launch
```

`--no-launch` installs the package but does not register the distro. Register it
without the interactive username prompt:

```powershell
& "$env:LOCALAPPDATA\Microsoft\WindowsApps\debian.exe" install --root
```

To keep it off the C: drive:

```powershell
wsl --shutdown
wsl --manage Debian --move D:\WSL\Debian
```

Base packages — Debian 13 is minimal, with no git, python or compiler:

```bash
apt-get update
apt-get install -y git wget curl build-essential ca-certificates bzip2
```

### VS Code

Install the **WSL** extension, then `Ctrl+Shift+P` → **WSL: Connect to WSL using
Distro** → Debian → **File → Open Folder**. The editor runs on Windows; the
terminal, interpreter and files are inside Linux. Connecting starts the distro —
no separate step.

### Clone

Clone inside the Linux filesystem, not `/mnt/c` or `/mnt/d`. Crossing into the
Windows filesystem is 10–20× slower, which matters because screening reads LMDB
heavily.

```bash
mkdir -p ~/GitRepos && cd ~/GitRepos
git clone https://github.com/bowen-gao/DrugCLIP.git
git clone https://github.com/THU-ATOM/DrugCLIP_screen_pipeline.git
```

`DrugCLIP_screen_pipeline` is a companion, not an alternative — it holds no
model. It builds the input LMDBs and post-processes results. Both are needed.

---

## Environment

Use **Miniforge**, not Miniconda or Anaconda:

```bash
wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -O miniforge.sh
bash miniforge.sh -b -p ~/miniforge3
~/miniforge3/bin/conda init bash
exec bash
```

Miniforge defaults to conda-forge only. Miniconda and Anaconda default to
`repo.anaconda.com`, whose terms require a paid licence for larger
organisations, and which blocks non-interactive installs with
`CondaToSNonInteractiveError`. Adding `nodefaults` to `environment.yml` does
**not** suppress this on conda 26 — the distribution has to change.

```bash
conda env create -f environment.yml
conda activate drugclip
```

---

## Uni-Core

Not on PyPI or conda; build from source:

```bash
git clone https://github.com/dptech-corp/Uni-Core.git ~/Uni-Core
cd ~/Uni-Core
pip install . --no-build-isolation
```

- **`--no-build-isolation` is required.** `setup.py` imports torch at module
  level, and pip's isolated build environment has no torch. Without it:
  `ModuleNotFoundError: No module named 'torch'`.
- **Do not pass `--disable-cuda-ext`.** The upstream README still documents it,
  but current `setup.py` inverted the logic — CUDA is now opt-in via
  `--enable-cuda-ext`, so a plain install is already CPU-only.

Verify:

```bash
python -c "
import torch, unicore
from unicore.modules import TransformerEncoderLayer, LayerNorm
from unicore.data import Dictionary
print('torch', torch.__version__, '| cuda', torch.cuda.is_available())
print('dict_mol', len(Dictionary.load('data/dict_mol.txt')), 'types')
"
```

`fused_layer_norm is not installed` and similar on import are expected on CPU —
Uni-Core reporting that its CUDA kernels are absent and falling back to plain
PyTorch.

---

## Model weights

**Nothing runs without these, and they are not in the repo.** Run
`usage/fetch_weights.ipynb`, which downloads from `bgao95/DrugCLIP_data`,
verifies the archive, extracts only the folds requested, and removes the archive
afterwards.

| | size | use |
| --- | --- | --- |
| `dude_identity_90.pt` | 1.2 GB | single model, the paper's benchmark setting |
| `6_folds/` | 7.2 GB | **the ensemble — use for real results** |
| `8_folds/` | 9.5 GB | 5HT2AR only |

The paper uses the ensemble for anything it acts on:

> we obtain six model weights through six-fold cross-validation. During virtual
> screening, these six model weights are used to generate six different
> predictions, which are then combined using mean pooling

Those six served NET, TRIP12 and the genome-wide screen, so they are
general-purpose. The folds are training-data splits, not targets — DrugCLIP is
zero-shot and no checkpoint is tuned to a protein.

Expected afterwards:

```
model/
  6_folds/fold_0.pt ... fold_5.pt
```

`model/` is gitignored.

---

## Running a screen

Input is a CSV of SMILES and a protein structure. Output is a ranked CSV.

Six notebooks: three ways of defining the pocket, each with a single-model and a
6-fold ensemble version.

| pocket strategy | single model | ensemble | when |
| --- | --- | --- | --- |
| from a bound ligand | `run_screen.ipynb` | `run_screen_6fold.ipynb` | the target has a holo PDB entry |
| transplanted by alignment | `run_screen_alphafold.ipynb` | `run_screen_alphafold_6fold.ipynb` | no structure, but a relative has a bound ligand |
| detected by Fpocket | `run_screen_fpocket.ipynb` | `run_screen_fpocket_6fold.ipynb` | no structure and no such relative |

Ranked best to worst — EF1% of 29.3, ~24 and 19.0 respectively on the paper's
benchmarks. Prefer alignment over Fpocket whenever a relative with a ligand
exists; the screen-pipeline README says only about half of Fpocket's pockets are
precise enough.

Everything configurable is in the first cell of each notebook: structure,
ligand, molecule CSV, weights, and `METHOD`, which names the output file.

### The alignment route

A predicted model has no ligand, so there is nothing to define a pocket around.
Rather than detecting one, this borrows one: TM-align superposes a solved
relative onto the model, and the same transform moves that relative's ligand into
the model's frame. The result is a pseudo-holo complex that
`pocket_from_pdb.py` consumes unchanged.

`usage/transplant.py` holds the helper. It drives `HomoAug/bin/TMalign`, already
shipped with the repo.

### Reading the output

Scores are cosine similarities, meaningful only relative to a background. A
ranking of a few hundred molecules gives their order, not whether any of them
binds. For a real campaign, spike the compounds of interest into 10k–100k
background molecules and use the z-score — the screen-pipeline README suggests
above 3, the Science paper used above 4 together with a Glide score below −6.

Compare runs by **rank, not score**: cosine values are not calibrated across
different pockets, so the same number against two pockets does not mean the same
thing.

---

## GPU

The notebooks detect a GPU and omit `--cpu` automatically, so they run
unchanged. For throughput:

```bash
conda env create -f environment-gpu.yml      # not environment.yml
conda activate drugclip-gpu
nvidia-smi
python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

Uni-Core must be rebuilt with its CUDA kernels:

```bash
cd ~/Uni-Core
python setup.py install --enable-cuda-ext
```

`pip install .` cannot pass that flag — `setup.py` reads it from `sys.argv`
directly. The CUDA version in the build environment must match the one PyTorch
was built against (`nvcc --version` against `torch.version.cuda`), or the
extensions compile but fail at import. A successful build shows itself by what
is absent: the `fused_layer_norm is not installed` messages should no longer
appear.

Then raise these in the screening cell of each notebook:

| setting | CPU | GPU |
| --- | --- | --- |
| `--fp16` | not passed | add, with `--fp16-init-scale 4 --fp16-scale-window 256` |
| `--batch-size` | 8 | 128–256 |
| `--num-workers` | 0 | 4–8 |

Also `chmod +x HomoAug/bin/*`, and adjust `REPO` and `PIPELINE` in the config
cells to the clone location.

**A GPU does not speed up conformer generation** — that is RDKit on CPU, roughly
0.16 s per molecule, about 20 hours for 460,000 single-threaded, on any
hardware. It parallelises across cores. With encoding moved to a GPU, conformer
generation becomes the bottleneck.

---

## Fpocket (optional)

Needed only for `run_screen_fpocket*.ipynb`. Not in Debian's repositories, so
build from source. Two things trip it up on Debian 13.

**Do not install `libqhull-dev`.** Fpocket bundles its own qhull. With the system
one present it links against that instead, builds cleanly, then reports **zero
pockets** at runtime rather than erroring — easily misread as "this protein has
no cavities":

```
QH6047 qhull input error: use upper-Delaunay('Qu') or infinity-point('Qz')
```

**gcc 14 rejects fpocket's C.** Incompatible pointer types are now errors. Relax
them *in the makefile* — passing `CFLAGS=` on the command line drops the include
paths, and `make` then exits 0 having built only the bundled qhull, leaving
`bin/` empty.

```bash
git clone https://github.com/Discngine/fpocket.git ~/fpocket
cd ~/fpocket
sed -i 's|^CFLAGS      = |CFLAGS      = -Wno-incompatible-pointer-types -Wno-int-conversion -Wno-implicit-function-declaration |' makefile
make            # serially: -j races, the makefile's dependencies are incomplete
ls bin/fpocket
```

Verify it finds cavities, not just that it exits 0:

```bash
cd /tmp && ~/fpocket/bin/fpocket -f model.pdb
ls model_out/pockets/*.pqr | wc -l     # expect tens, not zero
```

---

## Known traps

- **The embedding cache is keyed on filename, not contents.** Change the
  molecule library without clearing `emb/` and you silently score the previous
  molecules, with a plausible-looking result. The ensemble notebooks give each
  fold its own cache for the same reason — sharing one would serve fold 0's
  embeddings to every fold.

- **`pocket_from_pdb.py` swallows every error** in a bare `except: pass`. A
  structure that fails to parse yields an empty LMDB and no message. The
  notebooks assert the pocket is non-empty.

- **A transplant can fail silently.** Apply the transform backwards and the
  ligand lands outside the protein; the file is still valid and a meaningless
  pocket is extracted around whatever is nearby. The notebook checks TM-score,
  contact distance and burial first.

- **Bundled binaries may lack the execute bit** after cloning:
  `chmod +x HomoAug/bin/*`.

- **WSL inherits the Windows `PATH`.** Tools installed on Windows are reachable
  via `/mnt/c`, so a command can look installed when no Linux binary exists —
  check with `command -v`. The same `PATH` contains entries like
  `Program Files (x86)`, so an unquoted `export PATH=...` is a syntax error on
  the parentheses.

- **`wsl --manage --move` leaves the VHDX attached**, and `wsl --shutdown` does
  not release it. Starting the distro then fails with
  `ERROR_SHARING_VIOLATION`. From an elevated PowerShell:
  `Dismount-DiskImage -ImagePath "D:\WSL\Debian\ext4.vhdx"`. A reboot also
  clears it.

- **`gh auth login` defaults to SSH.** With an HTTPS remote, git still has no
  credential helper and the push fails with no useful message. Run
  `gh auth setup-git`.

---

## Validated versions

| package | version |
| --- | --- |
| python | 3.10.21 |
| pytorch | 2.10.0 (CPU) |
| rdkit | 2022.09.5 |
| numpy | 1.26.4 |
| pandas | 2.3.3 |
| scipy | 1.15.2 |
| scikit-learn | 1.7.2 |
| biopython | 1.88 |
| biopandas | 0.5.2 |
| lmdb | 2.3.0 |
| unicore | 0.0.1 (commit ace6fae) |

RDKit is pinned by the upstream README. Conformer generation changed between
versions, so a different one produces different coordinates and therefore
different scores — keep it fixed across machines you intend to compare.

torch 2.10 is far newer than the 2.0.0 Uni-Core's last release was built
against. Imports and module construction work; if runtime errors appear deep in
the encoder, pinning `pytorch-cpu=2.0.*` is the first thing to try.
