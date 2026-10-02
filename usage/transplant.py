"""Align a holo reference onto a model structure and transplant its ligand.

Produces a pseudo-holo complex that pocket_from_pdb.py can consume unchanged.
"""
import subprocess
import numpy as np
from pathlib import Path


def write_chain(pdb_in, chain, pdb_out):
    """Write only the protein ATOM records of one chain."""
    n = 0
    with open(pdb_out, "w") as out:
        for line in open(pdb_in):
            if line.startswith("ATOM") and line[21] == chain:
                out.write(line)
                n += 1
        out.write("TER\n")
    return n


def read_ligand(pdb_in, het_id, chain=None):
    """Return the HETATM lines for one ligand, and their coordinates."""
    lines, coords = [], []
    for line in open(pdb_in):
        if not line.startswith("HETATM"):
            continue
        if line[17:20].strip() != het_id:
            continue
        if chain is not None and line[21] != chain:
            continue
        lines.append(line.rstrip("\n"))
        coords.append([float(line[30:38]), float(line[38:46]), float(line[46:54])])
    return lines, np.array(coords)


def run_tmalign(tmalign, mobile, target, matrix_path):
    """Superpose `mobile` onto `target`. Returns (u, t, TM-score, stdout)."""
    out = subprocess.run(
        [str(tmalign), str(mobile), str(target), "-m", str(matrix_path)],
        capture_output=True, text=True, check=True).stdout

    tm = None
    for line in out.splitlines():
        # the TM-score normalised by the *target* (second structure)
        if line.startswith("TM-score=") and "Chain_2" in line:
            tm = float(line.split("=")[1].split("(")[0].strip())

    # TMalign's matrix file: rows 2-4 hold  m  t[m]  u[m][0..2]
    rows = open(matrix_path).read().splitlines()
    u, t = [], []
    for r in rows[2:5]:
        vals = [float(x) for x in r.split() if x not in ("",)][0:]
        t.append(vals[1])
        u.append(vals[2:5])
    return np.array(u), np.array(t), tm, out


def apply_transform(coords, u, t):
    """TMalign convention: X' = t + u . X"""
    return (u @ coords.T).T + t


def rewrite_hetatm(lines, new_coords):
    """Replace the coordinate columns of HETATM lines, preserving everything else."""
    out = []
    for line, c in zip(lines, new_coords):
        out.append("%s%8.3f%8.3f%8.3f%s" % (line[:30], c[0], c[1], c[2], line[54:]))
    return out


def min_distance_to_protein(pdb, coords):
    """Closest approach of `coords` to any protein atom - sanity check."""
    prot = []
    for line in open(pdb):
        if line.startswith("ATOM"):
            prot.append([float(line[30:38]), float(line[38:46]), float(line[46:54])])
    prot = np.array(prot)
    d = np.linalg.norm(prot[:, None, :] - coords[None, :, :], axis=2)
    return d.min(), (d.min(axis=0) < 6.0).sum(), len(np.unique(np.where(d < 6.0)[0]))


def transplant(model_pdb, holo_pdb, het_id, tmalign, out_pdb,
               holo_chain="A", workdir=None):
    workdir = Path(workdir or Path(out_pdb).parent)
    workdir.mkdir(parents=True, exist_ok=True)

    mobile = workdir / "_holo_chain.pdb"
    n_at = write_chain(holo_pdb, holo_chain, mobile)

    matrix = workdir / "_tm_matrix.txt"
    u, t, tm, log = run_tmalign(tmalign, mobile, model_pdb, matrix)

    lig_lines, lig_xyz = read_ligand(holo_pdb, het_id, chain=holo_chain)
    if len(lig_lines) == 0:
        raise SystemExit("no %s found in %s chain %s" % (het_id, holo_pdb, holo_chain))

    new_xyz = apply_transform(lig_xyz, u, t)
    new_lines = rewrite_hetatm(lig_lines, new_xyz)

    # write model + transplanted ligand
    with open(out_pdb, "w") as f:
        for line in open(model_pdb):
            if line.startswith(("ATOM", "TER")):
                f.write(line)
        for line in new_lines:
            f.write(line + "\n")
        f.write("END\n")

    before = min_distance_to_protein(model_pdb, lig_xyz)
    after = min_distance_to_protein(model_pdb, new_xyz)
    return {
        "tm_score": tm,
        "holo_chain_atoms": n_at,
        "ligand_atoms": len(lig_lines),
        "min_dist_before": before[0],
        "min_dist_after": after[0],
        "contacts_after": after[2],
        "out": str(out_pdb),
        "log": log,
    }


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("holo")
    ap.add_argument("het_id")
    ap.add_argument("out")
    ap.add_argument("--tmalign", default="/root/GitRepos/DrugCLIP/HomoAug/bin/TMalign")
    ap.add_argument("--holo-chain", default="A")
    a = ap.parse_args()
    info = transplant(a.model, a.holo, a.het_id, a.tmalign, a.out, a.holo_chain)
    info.pop("log")
    print(json.dumps(info, indent=2))
