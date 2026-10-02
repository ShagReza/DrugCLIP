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

### Claude Code inside the distro (optional)

If you use Claude Code, it has to be installed **inside** Debian. A Windows
install will not serve the VS Code WSL remote.

Beware: `claude --version` may appear to work already. WSL's PATH interop
reaches Windows executables under `/mnt/c`, so you can be running the Windows
binary without realising it. Check which one you actually have:

```bash
command -v claude      # /mnt/c/... means it is the Windows install
```

Install natively:

```bash
curl -fsSL https://claude.ai/install.sh | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
command -v claude      # expect ~/.local/bin/claude
```

Then in VS Code:

1. Extensions panel -> Claude Code -> **Install in WSL: Debian**
2. `Ctrl+Shift+P` -> **Developer: Reload Window**
3. Run `claude` once in the WSL terminal to authenticate - the Linux install
   has its own credentials, separate from any Windows one.

The same interop trap applies to other tools. If something "works" in the WSL
terminal, run `command -v` to confirm it is really Linux-side.

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
