# Running on Azure ML

Setup and run notes for an Azure ML compute instance. For the environment
itself and what each notebook does, see [SETUP.md](SETUP.md).

## 1. Compute instance

The work splits in two, and they want different things:

| step | bound by | helped by a GPU |
| --- | --- | --- |
| conformer generation, once per compound set | CPU, parallel across cores | no - this is RDKit |
| encoding, once per checkpoint | the model | yes, substantially |

So **cores matter whatever you pick**, and a GPU helps only the second half.

**With GPU quota.** An NC-series instance. DrugCLIP's encoder is small - a
15-layer transformer at 512 dimensions - so a T4 is ample and an A100 would sit
mostly idle. Choose on vCPU count rather than GPU grade: `NC16as_T4_v3`
(16 vCPU) over `NC4as_T4_v3` (4 vCPU), because those four cores would make
conformer generation the bottleneck by a wide margin.

**Without GPU quota.** The whole job runs on CPU, so take the highest core
count available to you - F-series (compute optimised) if offered, otherwise
D-series. Per-core pricing is usually flat within a family, which means a
larger instance costs about the same per unit of work and finishes sooner.

GPU quota often starts at zero on a new subscription and takes a day or two to
be granted. Check *Quotas* in the studio before planning around one; if the
N-series sizes are absent from the GPU tab entirely, that is quota or region
rather than anything you have selected wrongly.

**Settings.** Under *Security*, SSH can stay off - the VS Code Desktop button
uses its own tunnel - and root access is worth keeping for `apt-get`. Under
*Scheduling*, idle shutdown at 60 minutes is a reasonable default: it only
fires when the instance is genuinely idle, so a running job is safe, and the
timer starts after the work stops.

Open the instance with the **VS Code Desktop** button and authenticate normally.

## 2. Clone

Clone under your user folder. Anything at `~/cloudfiles/code/` sits beside
`Users/` rather than inside it, and the studio's file browser only shows the
`Users/<id>/` subtree.

```bash
cd ~/cloudfiles/code/Users/<your-id>
git clone https://github.com/ShagReza/DrugCLIP.git
git clone https://github.com/THU-ATOM/DrugCLIP_screen_pipeline.git
cd DrugCLIP
```

## 3. Environment

The ToS lines are needed because Azure's base conda is Anaconda, whose default
channels require accepting terms before a non-interactive install will run.

```bash
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
conda env create -f environment.yml
conda activate drugclip
pip install jupyter
```

Register the kernel - Azure does not pick up conda environments on its own, and
notebooks will not find the environment without this:

```bash
python -m ipykernel install --user --name drugclip --display-name "Python (drugclip)"
```

Then select **Python (drugclip)** from the kernel dropdown.

## 4. Uni-Core

```bash
git clone https://github.com/dptech-corp/Uni-Core.git ~/Uni-Core
cd ~/Uni-Core && pip install . --no-build-isolation
cd -
chmod +x HomoAug/bin/*
```

`--no-build-isolation` is required: `setup.py` imports torch, which pip's
isolated build environment does not have.

## 5. Paths

The notebooks ship with a hardcoded clone location:

```bash
sed -i 's|/root/GitRepos/DrugCLIP|'"$PWD"'|g' usage/*.ipynb
grep -c "root/GitRepos" usage/*.ipynb        # 0 for every file
```

`$PWD` works because you are in the repo root. Note this only rewrites paths -
`MOL_CSV` names the compound set and still needs setting by hand, in
`build_molecules.ipynb` and in whichever screening notebooks you run. Editing
it with `sed` is awkward because `.ipynb` files are JSON, so the quotes are
backslash-escaped on disk.

## 6. Order to run

1. `usage/fetch_weights.ipynb` - 13 GB download, extracts the six folds
2. Upload your compound CSV to `data/`
3. Set `MOL_CSV` in the notebooks you will run
4. `usage/build_molecules.ipynb` - once per compound set
5. The screening notebooks

## Running detached

Anything past the molecule build runs for hours, so run it from a terminal
rather than the notebook UI. `nohup` detaches it from the session: closing VS
Code, losing the connection or a terminal crash will not kill it.

```bash
cd ~/cloudfiles/code/Users/<your-id>/DrugCLIP
conda activate drugclip

nohup jupyter nbconvert --to notebook --execute usage/build_molecules.ipynb \
    --output build_executed.ipynb --ExecutePreprocessor.timeout=86400 \
    > build.log 2>&1 &
echo "pid $!"
```

Several in sequence, stopping if one fails:

```bash
cat > run_all.sh <<'EOF'
#!/bin/bash
set -e
source /anaconda/etc/profile.d/conda.sh
conda activate drugclip
cd ~/cloudfiles/code/Users/<your-id>/DrugCLIP

run () {
    echo "================ $1  $(date) ================"
    jupyter nbconvert --to notebook --execute "usage/$1.ipynb" \
        --output "$1_executed.ipynb" --ExecutePreprocessor.timeout=172800
}

run build_molecules
run run_screen_6fold

echo "================ done  $(date) ================"
ls -lh data/results/
EOF

chmod +x run_all.sh
nohup ./run_all.sh > run_all.log 2>&1 &
```

Put `run_screen_6fold` first among the screening notebooks: it populates the
per-fold embedding caches that the other ensembles then reuse.

### Watching it

```bash
pgrep -f nbconvert > /dev/null && echo RUNNING || echo "finished or died"
tail -f build.log                     # live
tail -20 build.log                    # snapshot
grep -E "built|wrote|Error|Traceback" build.log
```

A finished molecule build ends with:

```
built 460xxx | failed N
wrote .../data/lmdb/<name>.lmdb  (XXX MB, 460xxx molecules)
first record: mol_0  NN atoms, N conformers, shape (NN, 3)
```

That last line is the real check - it reads a record back out, so the file is
usable rather than merely present.

## Before deleting the instance

Everything under `~/cloudfiles/` is on the workspace file share and survives.
Anything in the home directory does not. Weights can be refetched; a screening
result that took hours cannot, so copy anything you want to keep:

```bash
cp data/results/*.csv ~/cloudfiles/code/Users/<your-id>/results/
```

## Timings

Measured on 12 cores locally; scale by core count.

| step | 460k molecules |
| --- | --- |
| conformer generation, once | ~30 min on 46 workers |
| encoding, per checkpoint | 1-3 h |
| six folds | 6 checkpoints, not 6 per notebook - the cache is shared |

A GPU would cut the encoding sharply but does nothing for conformer
generation, which is RDKit on CPU whatever the hardware.
