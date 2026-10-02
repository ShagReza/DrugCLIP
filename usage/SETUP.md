# Setup

Reproducible environment for running DrugCLIP. Validated on Debian 13 under
WSL2, CPU-only, on 2026-09-30.

The upstream repo ships no dependency manifest ("Requirements: same as
Uni-Mol"), so this file and `environment.yml` reconstruct one.

## What this gets you

A CPU environment that can **screen** - encode a molecule library once, cache
the embeddings, and rank molecules against a pocket by cosine similarity.

It does **not** get you training. Training needs an NVIDIA GPU: the loss and
the training-time `forward()` call `.cuda()` unconditionally
(`unimol/losses/cross_entropy.py`, `unimol/models/drugclip.py`), and the
published runs used 4-8x A100.

## Prerequisites

Linux, or WSL2 on Windows. Native Windows does not work: Uni-Core is
distributed and built for Linux, and the HomoAug helpers are Linux ELF
binaries.

## 0. WSL2 (Windows only)

Skip this section on a native Linux machine or a cloud VM.

### Create a distro

```powershell
wsl --install Debian --no-launch
```

`--no-launch` installs the package but does **not** register the distro -
`wsl --list` will show nothing. Register it without the interactive
username prompt:

```powershell
& "$env:LOCALAPPDATA\Microsoft\WindowsApps\debian.exe" install --root
```

### Move it off C: (optional)

WSL stores its disk under `%LOCALAPPDATA%` on C: by default. If C: is tight:

```powershell
wsl --shutdown
wsl --manage Debian --move D:\WSL\Debian
```

`--location` and `--name` on `wsl --install` need a newer WSL than 2.3.24;
`--manage --move` is the portable way.

**Known bug:** `--manage --move` leaves the VHDX attached at the Windows
storage layer, and `wsl --shutdown` does not release it. Starting the distro
then fails with:

```
Failed to attach disk 'D:\WSL\Debian\ext4.vhdx' to WSL2:
The process cannot access the file because it is being used by another process.
Error code: Wsl/Service/CreateInstance/MountVhd/HCS/ERROR_SHARING_VIOLATION
```

Fix from an **elevated** PowerShell:

```powershell
Dismount-DiskImage -ImagePath "D:\WSL\Debian\ext4.vhdx"
(Get-DiskImage -ImagePath "D:\WSL\Debian\ext4.vhdx").Attached   # expect False
```

A reboot also clears it.

### Base packages

Debian 13 is minimal - no git, python, or compiler. It also has no `which`,
so use `command -v`.

```bash
apt-get update
apt-get install -y git wget curl build-essential ca-certificates bzip2
```

### Open it in VS Code

Install the **WSL** extension (`ms-vscode-remote.remote-wsl`), then:

1. `Ctrl+Shift+P` -> **WSL: Connect to WSL using Distro...** -> **Debian**
2. **File -> Open Folder** -> `/root/GitRepos/DrugCLIP`

The bottom-left status bar should read `WSL: Debian`. The editor runs on
Windows; the terminal, Python interpreter and files are all inside Linux.

You do not need to start WSL by hand - connecting launches the distro. (This
differs from Docker Desktop, which is a Windows app that must be running
first.)

To work on both repos at once, use a multi-root workspace:
**File -> Add Folder to Workspace...** -> `/root/GitRepos/DrugCLIP_screen_pipeline`.
That shows them side by side and lets search span both; it does not merge
them - they stay separate git repositories with their own remotes.

### A note on PATH interop

WSL inherits the Windows `PATH`, so tools installed on Windows are reachable
from inside Linux via `/mnt/c/...`. That means a command can appear to be
installed when it is not actually present on the Linux side, which breaks
anything that needs a genuine Linux binary - including VS Code remote
extensions. Check with:

```bash
command -v <tool>      # a /mnt/c/... path means it is the Windows install
```

### Clone the repos

Clone **inside** the Linux filesystem, not on `/mnt/c` or `/mnt/d` - crossing
into the Windows filesystem is roughly 10-20x slower, which matters because
screening reads LMDB heavily.

```bash
mkdir -p ~/GitRepos && cd ~/GitRepos
git clone https://github.com/bowen-gao/DrugCLIP.git
git clone https://github.com/THU-ATOM/DrugCLIP_screen_pipeline.git
```

If you are working from a fork, clone your fork and add the original as
`upstream` so you can pull in later fixes:

```bash
cd ~/GitRepos/DrugCLIP
git remote add upstream https://github.com/bowen-gao/DrugCLIP.git

cd ~/GitRepos/DrugCLIP_screen_pipeline
git remote add upstream https://github.com/THU-ATOM/DrugCLIP_screen_pipeline.git
```

`DrugCLIP_screen_pipeline` is a companion repo, not an alternative. It holds
no model - it builds the input LMDBs (`pocket_from_pdb.py`, `mol_from_tsv.py`,
`SDF2lmdb.py`) and post-processes results (clustering, novelty filtering,
docking). You need both to run a screen.

## 1. Conda

Use **Miniforge**, not Miniconda or Anaconda.

```bash
wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -O miniforge.sh
bash miniforge.sh -b -p ~/miniforge3
~/miniforge3/bin/conda init bash
exec bash
```

Miniforge defaults to conda-forge only. Miniconda/Anaconda default to
`repo.anaconda.com`, whose Terms of Service require a paid licence for larger
organisations, and which block non-interactive installs with
`CondaToSNonInteractiveError`. Putting `nodefaults` in `environment.yml` does
**not** suppress this on conda 26 - the distribution has to be changed.

## 2. Environment

```bash
conda env create -f environment.yml
conda activate drugclip
```

## 3. Uni-Core

Not on PyPI or conda; build from source.

```bash
git clone https://github.com/dptech-corp/Uni-Core.git ~/Uni-Core
cd ~/Uni-Core
pip install . --no-build-isolation
```

Two things that are easy to get wrong:

- **`--no-build-isolation` is required.** `setup.py` imports `torch` at module
  level, and pip's isolated build environment does not have it. Without the
  flag you get `ModuleNotFoundError: No module named 'torch'`.

- **Do not pass `--disable-cuda-ext`.** The upstream README still documents it,
  but current `setup.py` inverted the logic: `DISABLE_CUDA_EXTENSION = True` is
  the default and CUDA is opt-in via `--enable-cuda-ext`. A plain install is
  already CPU-only.

For a GPU machine, add `--enable-cuda-ext` and replace `pytorch-cpu` with a
CUDA build in `environment.yml`.

## 4. Verify

```bash
python -c "
import torch, unicore
from unicore.modules import TransformerEncoderLayer, LayerNorm
from unicore.data import Dictionary
print('torch', torch.__version__, '| cuda', torch.cuda.is_available())
print('dict_mol', len(Dictionary.load('data/dict_mol.txt')), 'types')
"
```

On import you will see:

```
fused_multi_tensor is not installed corrected
fused_layer_norm is not installed corrected
...
```

These are expected and harmless on CPU. Uni-Core is reporting that its fused
CUDA kernels are absent and that it is falling back to plain PyTorch.

## Validated versions

| Package | Version |
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

Note that torch 2.10 is far newer than the 2.0.0 Uni-Core's last release
(v0.0.3, June 2023) was built against. Imports and module construction work.
If you hit runtime errors deep in the encoder, pinning `pytorch-cpu=2.0.*` is
the first thing to try.

## Still needed to actually screen

This environment is the software side only. You also need:

1. **Model weights** - not in this repo. Google Drive link in `README.md`
   (NeurIPS version), or `bgao95/DrugCLIP_data` on HuggingFace for the Science
   version. The published results use a 6-fold ensemble; the default here is a
   single model.

2. **Input LMDBs** - `mols.lmdb` and `pocket.lmdb`. Build them with
   `py_scripts/write_dude_multi.py`, or more conveniently with
   `pocket_from_pdb.py` / `mol_from_tsv.py` from
   [DrugCLIP_screen_pipeline](https://github.com/THU-ATOM/DrugCLIP_screen_pipeline).

3. **A patch for CPU inference.** `retrieval.py` respects a `--cpu` flag, but
   `unicore.utils.move_to_cuda(sample)` is called unconditionally inside
   `retrieve_mols()` and `encode_mols_once()` in `unimol/tasks/drugclip.py`.
   That needs to become device-aware before a CPU run will work.

## WSL2 gotchas

Beyond the disk-lock bug in section 0:

- WSL inherits the Windows `PATH`, which contains entries like
  `Program Files (x86)`. An unquoted `export PATH=...:$PATH` in a
  non-interactive shell is a syntax error on the parentheses. Use conda's
  absolute path in scripts, or quote the assignment.

- A distro shuts itself down after a period of inactivity and restarts on
  demand. No manual start is needed.


## Optional: Fpocket

Only needed for `run_screen_fpocket.ipynb` — the route for a target with no
relative carrying a bound ligand. Skip it otherwise.

Fpocket is not in Debian's repositories, so it has to be built from source. Two
things trip it up on Debian 13.

### Do not install libqhull-dev

Fpocket bundles its own copy of qhull, the geometry library it uses to find
cavities. If the system `libqhull-dev` is present, fpocket links against that
instead, and the two expect different option syntax. It builds cleanly and then
dies at runtime:

```
QH6047 qhull input error: use upper-Delaunay('Qu') or infinity-point('Qz')
with Delaunay('d') or Voronoi('v')
While executing: rbox D3 | qvoronoi p i Pp Qz Qt
```

Note it reports zero pockets rather than failing outright, so this is easy to
misread as "the protein has no cavities". If it is already installed:

```bash
apt-get remove -y libqhull-dev
```

then rebuild from clean.

### gcc 14 rejects fpocket's C

Debian 13 ships gcc 14, which promotes incompatible pointer types from warning
to error. Fpocket's source predates that:

```
src/fparams.c:296:24: error: passing argument 1 of 'strcpy' from
incompatible pointer type [-Wincompatible-pointer-types]
make: *** [makefile:148: obj/fparams.o] Error 1
```

Relax those three checks in the makefile. Edit `CFLAGS` rather than passing
`CFLAGS=` on the command line — overriding it drops the include paths the build
needs, and `make` then exits 0 having built only the bundled qhull, leaving
`bin/` empty.

### Build

```bash
git clone https://github.com/Discngine/fpocket.git ~/fpocket
cd ~/fpocket
sed -i 's|^CFLAGS      = |CFLAGS      = -Wno-incompatible-pointer-types -Wno-int-conversion -Wno-implicit-function-declaration |' makefile
make -j4
ls bin/fpocket          # should exist
```

Verify it actually finds cavities, not just that it exits 0:

```bash
cd /tmp && fpocket -f some_model.pdb
ls some_model_out/pockets/*.pqr | wc -l     # expect tens, not zero
```

On the human PGK2 AlphaFold model this gives 32 cavities. A count of zero means
the qhull conflict above.

Point `FPOCKET` in the notebook's config cell at `~/fpocket/bin/fpocket`.


## Running on a GPU

The encoding step is what a GPU accelerates, and the ensemble notebooks repeat
it once per fold, so this is where the time goes on a large library.

**Conformer generation gets no benefit.** That is RDKit on CPU - roughly 0.16 s
per molecule, about 20 hours for 460,000 single-threaded, on any hardware. It
parallelises across cores, which is the lever that matters there.

Tested on Azure ML Compute Instances, which are Ubuntu; a plain Azure or GCP
GPU VM works the same way. Databricks also works but fights this workload: its
clusters are ephemeral, so Uni-Core and fpocket get rebuilt on every start
unless baked into a custom container.

### 1. Environment

```bash
git clone https://github.com/ShagReza/DrugCLIP.git
cd DrugCLIP
conda env create -f environment-gpu.yml      # not environment.yml
conda activate drugclip-gpu
```

The only difference is a CUDA build of PyTorch. RDKit stays pinned to the same
version so scores remain comparable with CPU runs.

Confirm the GPU is visible before going further:

```bash
nvidia-smi
python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

### 2. Uni-Core, with CUDA kernels

The CPU instructions rely on CUDA extensions being skipped by default. Here you
want them, which means opting in:

```bash
git clone https://github.com/dptech-corp/Uni-Core.git ~/Uni-Core
cd ~/Uni-Core
python setup.py install --enable-cuda-ext
```

`pip install .` has no clean way to pass that flag, since `setup.py` reads it
from `sys.argv` directly, so use `setup.py install` here.

The CUDA version in the build environment must match the one PyTorch was built
against, or the extensions compile but fail at import. Check with:

```bash
nvcc --version
python -c "import torch; print(torch.version.cuda)"
```

A successful build is visible at import time by what is *absent* - the
`fused_layer_norm is not installed` messages seen on CPU should no longer
appear.

### 3. Other binaries

```bash
chmod +x HomoAug/bin/*            # git does not always preserve the bit
```

Rebuild fpocket if using those notebooks - see the Fpocket section above; the
qhull and gcc caveats apply equally.

### 4. Notebook settings

The notebooks already detect the GPU and omit `--cpu` when one is present, so
they run unchanged. Three settings are worth raising for throughput, in the
screening cell of each:

| setting | CPU default | on GPU |
| --- | --- | --- |
| `--fp16` | not passed | add it, with `--fp16-init-scale 4 --fp16-scale-window 256` |
| `--batch-size` | 8 | 128-256 |
| `--num-workers` | 0 | 4-8 |

`--fp16` is deliberately absent on CPU because half precision is a GPU path.

Paths in the config cells assume `/root/GitRepos/DrugCLIP`; adjust `REPO` and
`PIPELINE` to wherever the repos were cloned.

### What to expect

For 460,000 molecules with the 6-fold ensemble:

| | CPU (12 cores) | GPU |
| --- | --- | --- |
| conformer generation, once | ~20 h single-threaded, ~2-3 h parallelised | the same - CPU bound |
| encoding, six folds | 10-48 h | well under an hour |

So a GPU removes the encoding bottleneck and leaves conformer generation as the
limit. Build `mols.lmdb` once and point every notebook at it rather than
regenerating it per notebook.

## Running a screen

Two notebooks in this folder, both validated on CPU.

### `run_screen.ipynb` — from an experimental structure

For a target that has a holo PDB entry (a structure with a ligand bound).
Downloads the structure, extracts the pocket around a chosen ligand, builds
conformers from a SMILES CSV, and ranks.

### `run_screen_alphafold.ipynb` — from a predicted structure

For a target with no experimental structure. Human PGK2 (P07205) is the worked
example — it has no PDB entry.

A predicted model has no ligand, so there is nothing to define a pocket around.
Rather than detecting one with Fpocket, this borrows one: TM-align superposes a
solved relative onto the model, and the same transform moves that relative's
ligand into the model's coordinate frame. The result is a pseudo-holo complex
that `pocket_from_pdb.py` consumes unchanged.

This follows the screen-pipeline README's own advice — align to an experimental
ligand rather than using Fpocket, which it says yields usable pockets only about
half the time. On the paper's benchmarks the aligned route reaches EF1% ~24 on
AlphaFold2 structures, against 19.0 for Fpocket.

`transplant.py` holds the alignment helper. It drives `HomoAug/bin/TMalign`,
already shipped with this repo.

Worked example, human PGK2 against mouse Pgk2 (2PAA):

| Check | Value |
| --- | --- |
| plDDT (model confidence) | median 97.8, 95% above 90 |
| TM-score | 0.9825 |
| Closest ligand–protein contact | 2.73 A |
| Pocket size vs experimental | 199 vs 215 atoms |

### Things that bite

- **The embedding cache is keyed on filename, not contents.** Change the
  molecule library without clearing `emb/` and you will silently score the
  previous molecules, with a perfectly plausible-looking result.

- **`pocket_from_pdb.py` swallows every error** in a bare `except: pass`. A
  structure that fails to parse yields an empty LMDB and no message. Both
  notebooks assert the pocket is non-empty.

- **A transplant can fail silently.** Apply the transform backwards and the
  ligand lands outside the protein; the file is still valid and a meaningless
  pocket is still extracted around whatever happens to be nearby. The notebook
  checks TM-score, contact distance and burial before trusting it.

- **The bundled binaries may lack the execute bit** after cloning. Run
  `chmod +x HomoAug/bin/*`.

### Scores are relative

The output is a cosine similarity, meaningful only against a background. A
ranking of a handful of molecules tells you their order, not whether any of them
binds. For a real campaign, spike the compounds of interest into 10k–100k
background molecules and use the z-score — the screen-pipeline README suggests
above 3, the Science paper used above 4 together with a Glide score below −6.

## Pushing to a fork

`gh auth login` defaults to the SSH protocol. If the remote is HTTPS, git still
has no credential helper afterwards and the push fails with no useful message.
Run:

```bash
gh auth setup-git
```
