# PDOS Local Ground Truth Evidence Extractor — v0.1

## 什么是这个工具 / What is this?

这是一个本地命令行工具，用于从您的 STL 文件中提取网格拓扑证据，生成机器可读的 JSON 报告。

This is a **local command-line tool** that reads a local STL file and
deterministically extracts mesh topology evidence, writing two JSON files:

| 输出文件 / Output file | 说明 |
|---|---|
| `PRIMARY_MESH_EVIDENCE.json` | 主要证据文件（顶点、三角面、边界等） |
| `PRIMARY_MESH_EVIDENCE.validation.json` | 验证门结果（PASS / PASS_WITH_WARNINGS / FAIL） |

**这个版本不做以下工作 / This version does NOT do:**
- Feature / curvature / ridge / valley detection
- Measurement Cage integration
- Design Prior integration
- Patch candidates / patch layout
- U/V flow parameterization
- Loft / Sweep / NetworkSrf
- G0 / G1 / G2 continuity decisions
- NURBS surface generation
- Rhino surface construction
- Any use of existing Phase 1.2 JSON files as input data

All computed values come **strictly** from the user-supplied STL file.

---

## 使用要求 / Requirements

- **Python 3.11 or later** is required.
  - Check your Python version: open Terminal and type `python3 --version`
  - Download from https://www.python.org/downloads/macos/
- **No external packages required.** Only Python standard library is used.

---

## 本地安装步骤 / Local setup (macOS)

Open Terminal (Finder → Applications → Utilities → Terminal).

```bash
# 1. Go to the project directory
cd /path/to/pdos-local-extractor

# 2. (Optional but recommended) Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Confirm Python version
python --version
# Should show Python 3.11.x or later
```

No `pip install` step is required — there are no external dependencies.

---

## 如何运行 / How to run

### Mac 双击运行（推荐）/ Double-click on macOS (recommended)

1. 在 Finder 中打开 `pdos-local-extractor` 文件夹。
2. 双击 `run_extractor_macos.command`。
3. 按窗口提示，把 STL 文件拖进终端窗口，然后按 Enter。
4. 两个 JSON 会生成在 STL 文件所在目录。

On first use, macOS may require the executable bit to be set. Run once:

```bash
cd /path/to/pdos-local-extractor
chmod +x run_extractor_macos.command
```

The helper performs no upload or network request. It does not read old Phase
1.2 JSON files and it stops after Phase 1.2A evidence and validation output.

### Basic command

```bash
python -m pdos_extractor \
  --input "/Users/your-name/Desktop/my_model.stl" \
  --output "./PRIMARY_MESH_EVIDENCE.json"
```

### All options

```bash
python -m pdos_extractor \
  --input  PATH_TO_STL \
  --output PATH_TO_OUTPUT_JSON \
  --topology-mode  raw|welded|both \
  --symmetry-mode  off|optional \
  --weld-tolerance NON_NEGATIVE_FINITE_FLOAT \
  --fail-on-validation-error
```

| 选项 / Option | 默认值 / Default | 说明 |
|---|---|---|
| `--input` | *(required)* | STL 文件路径 |
| `--output` | `./PRIMARY_MESH_EVIDENCE.json` | 输出 JSON 文件路径 |
| `--topology-mode` | `both` | `raw` = 精确匹配；`welded` = 容差合并；`both` = 两者都算 |
| `--symmetry-mode` | `off` | 对称分析（v0.1 中默认 UNKNOWN） |
| `--weld-tolerance` | `1e-8 × bbox diagonal` | welded 模式容差；拒绝负数、NaN 和 Infinity |
| `--fail-on-validation-error` | off | 验证 FAIL 时返回非零退出码（PASS_WITH_WARNINGS 不会触发） |

---

## 如何设置 Mac 本地 STL 路径 / How to set a Mac STL path

### 文件名包含中文或空格时

Mac Terminal 支持含中文和空格的路径，**务必用引号包裹**：

```bash
python -m pdos_extractor \
  --input "/Users/your-name/Desktop/第一个建模参考图。.stl" \
  --output "./PRIMARY_MESH_EVIDENCE.json"
```

### 使用桌面路径示例

```bash
python -m pdos_extractor \
  --input "/Users/your-name/Desktop/第一个建模参考图。.stl" \
  --output "/Users/your-name/Desktop/PRIMARY_MESH_EVIDENCE.json" \
  --topology-mode both \
  --fail-on-validation-error
```

> 💡 **Tip:** In Finder, drag and drop the STL file into Terminal to automatically
> paste the full path.

---

## 输出文件说明 / Output files

After running the command, you will find:

```
PRIMARY_MESH_EVIDENCE.json          ← main evidence file
PRIMARY_MESH_EVIDENCE.validation.json ← validation gate result
```

### PRIMARY_MESH_EVIDENCE.json

Contains:
- Input file info: filename, byte size, SHA-256 hash, detected format
- Topology for each variant (`raw_exact` and/or `welded`):
  - Triangle / vertex / edge counts
  - Bounding box (min, max, extents, diagonal)
  - Connected component count and per-component statistics
  - Boundary edge count
  - Closed boundary loops, open chains, and branched graph evidence
  - Non-manifold edge count
  - Watertight flag
  - Euler characteristic (V − E + F)
- Full provenance for every computed field

### Provenance inheritance

To keep the JSON readable, provenance is inherited at object level:

- `input.provenance` applies to file size, SHA-256, format, units status,
  triangle count, and parse result.
- Each `topology.<variant>.provenance` applies to its vertex/edge/face counts,
  bounding box, components, boundary records, non-manifold count, watertight
  result, Euler characteristic, and weld tolerance.
- Component and boundary coordinates are copied only from canonical vertices
  derived from STL vertex records. No fallback coordinate is generated.

Only `OBSERVED`, `DERIVED`, and `UNKNOWN` are valid epistemic provenance
statuses. A malformed STL produces `STL_PARSE_ERROR`; partial triangles are not
passed to topology analysis.

### PRIMARY_MESH_EVIDENCE.validation.json

Contains a machine-readable **Validation Gate** result:

| Overall status | Meaning |
|---|---|
| `PASS` | All hard checks passed, no warnings |
| `PASS_WITH_WARNINGS` | Hard checks passed, but e.g. raw/welded differ |
| `FAIL` | One or more hard checks failed |

Hard gate checks include:
- Input file exists
- SHA-256 present and valid (64-hex-char)
- STL parse success
- Triangle count > 0
- Output JSON strict parse round-trip success
- Topology source is STL only
- Measurement cage used = false
- Design prior used = false
- Synthetic geometry count = 0
- Hardcoded feature coordinate count = 0
- Untraceable primary evidence count = 0
- Phase 1.3 decision count = 0
- Contamination detected = false

`UNKNOWN` values are **legal** and do not cause FAIL by themselves.

---

## 如何读懂 Validation Gate / How to read the Validation Gate

Open `PRIMARY_MESH_EVIDENCE.validation.json` in any text editor.

Look for:

```json
{
  "overall_status": "PASS",
  "warnings": [],
  "checks": { ... }
}
```

- **`PASS`** → everything is clean. Evidence is fully traceable.
- **`PASS_WITH_WARNINGS`** → evidence is valid but e.g. raw and welded topology
  variants differ. Read `"warnings"` for details.
- **`FAIL`** → at least one hard check failed. Read `"checks"` to find which
  check has `"result": false` while `"expected": true`.

---

## 运行测试 / Running tests

```bash
cd pdos-local-extractor
python -m unittest discover -s tests -v
```

The standard-library test suite includes more than 100 deterministic tests.
No real user STL is required or committed. It covers malformed/truncated STL,
binary headers beginning with `solid`, Chinese paths, distance-based welding,
closed/open/branched boundaries, strict JSON, validation outcomes, and CLI exit
behavior.

---

## 版本说明 / Version note

**v0.1** — Phase 1.2A only.
No feature detection, no surface construction, no Rhino output.
Stop after PRIMARY_MESH_EVIDENCE is produced; do not enter Phase 1.3.

Real user STL validation was NOT run in cloud CI.
