"""
tests/test_hpc_drawio_layout.py
--------------------------------
Positional tests for hpc_oke.drawio.

Strategy:
  1. Parse hpc_oke.drawio and extract every mxCell with geometry.
  2. Assert structural / positional relationships derived from the reference PNG:
       - Correct elements exist
       - Correct containment (element centres inside containing boxes)
       - Correct ordering (subnets stack top-to-bottom)
       - FD columns are side-by-side at equal width
       - Subnets span full VCN width
       - HPC nodes centred in their respective FD columns
       - Gateways inside region but right of VCN
       - External services outside region right edge
       - Instance Pool box wraps all three HPC nodes

All tests are deterministic — no LLM, no OCI SDK.
"""
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

DRAWIO_PATH = Path(__file__).parent.parent / "hpc_oke.drawio"
TOLERANCE   = 30   # px — allowed position delta for containment / alignment checks


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_cells(path: Path) -> dict[str, dict]:
    """Return {cell_id: {x,y,w,h,value,style}} for every cell with geometry."""
    tree = ET.parse(path)
    cells: dict[str, dict] = {}
    for cell in tree.iter("mxCell"):
        geo = cell.find("mxGeometry")
        if geo is None:
            continue
        cells[cell.get("id", "")] = {
            "x": float(geo.get("x", 0)),
            "y": float(geo.get("y", 0)),
            "w": float(geo.get("width",  0)),
            "h": float(geo.get("height", 0)),
            "value": cell.get("value", ""),
            "style": cell.get("style", ""),
        }
    return cells


def _cx(c): return c["x"] + c["w"] / 2
def _cy(c): return c["y"] + c["h"] / 2
def _right(c): return c["x"] + c["w"]
def _bottom(c): return c["y"] + c["h"]


def _contains_x(outer, inner, tol=TOLERANCE):
    """inner x-span is within outer x-span (with tolerance)."""
    return (inner["x"] >= outer["x"] - tol and
            _right(inner) <= _right(outer) + tol)


def _contains_y(outer, inner, tol=TOLERANCE):
    return (inner["y"] >= outer["y"] - tol and
            _bottom(inner) <= _bottom(outer) + tol)


def _centre_in_box(box, icon_x, tol=TOLERANCE):
    """Icon centre x is within TOLERANCE of box centre x."""
    return abs((icon_x + 24) - _cx(box)) <= tol   # 24 = icon half-width


@pytest.fixture(scope="module")
def cells():
    assert DRAWIO_PATH.exists(), f"hpc_oke.drawio not found at {DRAWIO_PATH}"
    return _parse_cells(DRAWIO_PATH)


def _get(cells, cid, suffix="_g"):
    """Return cell by id; tries bare id first, then id+suffix."""
    if cid in cells:
        return cells[cid]
    if cid + suffix in cells:
        return cells[cid + suffix]
    return None


# ── 1. Required elements exist ────────────────────────────────────────────────

class TestRequiredElements:
    REQUIRED = [
        "region_box", "ad1_box",
        "fd1_box", "fd2_box", "fd3_box",
        "vcn_box",
        "cp_sub", "worker_sub", "op_sub", "bas_sub",
        "inst_pool_box",
        "oke_1_g", "hpc_1_g", "hpc_2_g", "hpc_3_g",
        "operator_1_g", "bastion_1_g",
        "nat_gw_g", "sgw_1_g", "igw_1_g",
        "pv_1_g", "fss_1_g",
        "objstr_1_g", "monitor_1_g", "iam_1_g",
    ]

    @pytest.mark.parametrize("cid", REQUIRED)
    def test_element_exists(self, cells, cid):
        assert cid in cells, f"Missing element: {cid}"

    def test_rdma_label_exists(self, cells):
        rdma_cells = [v for v in cells.values()
                      if "RDMA" in v["value"] or "RoCE" in v["value"]]
        assert rdma_cells, "RDMA + RoCE Network label missing"


# ── 2. Containment: subnets inside VCN ───────────────────────────────────────

class TestSubnetContainment:
    SUBNETS = ["cp_sub", "worker_sub", "op_sub", "bas_sub"]

    @pytest.mark.parametrize("sid", SUBNETS)
    def test_subnet_inside_vcn_x(self, cells, sid):
        vcn = cells["vcn_box"]
        sub = cells[sid]
        assert _contains_x(vcn, sub), (
            f"{sid} x-span [{sub['x']:.0f}–{_right(sub):.0f}] not inside "
            f"VCN [{vcn['x']:.0f}–{_right(vcn):.0f}]")

    @pytest.mark.parametrize("sid", SUBNETS)
    def test_subnet_inside_vcn_y(self, cells, sid):
        vcn = cells["vcn_box"]
        sub = cells[sid]
        assert _contains_y(vcn, sub), (
            f"{sid} y-span [{sub['y']:.0f}–{_bottom(sub):.0f}] not inside "
            f"VCN [{vcn['y']:.0f}–{_bottom(vcn):.0f}]")


# ── 3. Subnet rows span full VCN width ────────────────────────────────────────

class TestSubnetFullWidth:
    SUBNETS = ["cp_sub", "worker_sub", "op_sub", "bas_sub"]

    @pytest.mark.parametrize("sid", SUBNETS)
    def test_subnet_nearly_full_vcn_width(self, cells, sid):
        vcn = cells["vcn_box"]
        sub = cells[sid]
        # Subnet width should be at least 90% of VCN width
        assert sub["w"] >= vcn["w"] * 0.90, (
            f"{sid} width {sub['w']:.0f} < 90% of VCN width {vcn['w']:.0f}")


# ── 4. Subnet vertical ordering ───────────────────────────────────────────────

class TestSubnetOrdering:
    def test_cp_above_worker(self, cells):
        assert cells["cp_sub"]["y"] < cells["worker_sub"]["y"]

    def test_worker_above_operator(self, cells):
        assert cells["worker_sub"]["y"] < cells["op_sub"]["y"]

    def test_operator_above_bastion(self, cells):
        assert cells["op_sub"]["y"] < cells["bas_sub"]["y"]

    def test_cp_and_worker_do_not_overlap(self, cells):
        assert _bottom(cells["cp_sub"]) <= cells["worker_sub"]["y"] + TOLERANCE

    def test_worker_and_operator_do_not_overlap(self, cells):
        assert _bottom(cells["worker_sub"]) <= cells["op_sub"]["y"] + TOLERANCE

    def test_operator_and_bastion_do_not_overlap(self, cells):
        assert _bottom(cells["op_sub"]) <= cells["bas_sub"]["y"] + TOLERANCE


# ── 5. FD columns side-by-side, equal size ───────────────────────────────────

class TestFaultDomains:
    def test_fd1_left_of_fd2(self, cells):
        assert cells["fd1_box"]["x"] < cells["fd2_box"]["x"]

    def test_fd2_left_of_fd3(self, cells):
        assert cells["fd2_box"]["x"] < cells["fd3_box"]["x"]

    def test_fds_same_width(self, cells):
        w1 = cells["fd1_box"]["w"]
        w2 = cells["fd2_box"]["w"]
        w3 = cells["fd3_box"]["w"]
        assert abs(w1 - w2) <= TOLERANCE, f"FD1 w={w1} FD2 w={w2}"
        assert abs(w2 - w3) <= TOLERANCE, f"FD2 w={w2} FD3 w={w3}"

    def test_fds_same_height(self, cells):
        h1 = cells["fd1_box"]["h"]
        h2 = cells["fd2_box"]["h"]
        h3 = cells["fd3_box"]["h"]
        assert abs(h1 - h2) <= TOLERANCE
        assert abs(h2 - h3) <= TOLERANCE

    def test_fds_no_horizontal_gap(self, cells):
        """FDs should be adjacent (gap < TOLERANCE)."""
        fd1, fd2, fd3 = cells["fd1_box"], cells["fd2_box"], cells["fd3_box"]
        gap12 = fd2["x"] - _right(fd1)
        gap23 = fd3["x"] - _right(fd2)
        assert gap12 <= TOLERANCE, f"Gap between FD1 and FD2: {gap12:.0f}"
        assert gap23 <= TOLERANCE, f"Gap between FD2 and FD3: {gap23:.0f}"

    def test_fds_span_vcn_width(self, cells):
        """Combined FD span should cover most of VCN width."""
        vcn  = cells["vcn_box"]
        fd1  = cells["fd1_box"]
        fd3  = cells["fd3_box"]
        span = _right(fd3) - fd1["x"]
        assert span >= vcn["w"] * 0.85, (
            f"FD span {span:.0f} < 85% of VCN width {vcn['w']:.0f}")


# ── 6. Icon placement: OKE in CP Subnet / FD2 ────────────────────────────────

class TestIconPlacement:
    def test_oke_inside_cp_subnet_y(self, cells):
        cp  = cells["cp_sub"]
        oke = cells["oke_1_g"]
        assert _contains_y(cp, oke, tol=TOLERANCE), (
            f"OKE y={oke['y']:.0f} not inside CP Subnet "
            f"[{cp['y']:.0f}–{_bottom(cp):.0f}]")

    def test_oke_centred_in_fd2(self, cells):
        fd2 = cells["fd2_box"]
        oke = cells["oke_1_g"]
        assert _centre_in_box(fd2, oke["x"]), (
            f"OKE centre {oke['x']+24:.0f} not near FD2 centre {_cx(fd2):.0f}")

    @pytest.mark.parametrize("hpc_id,fd_id", [
        ("hpc_1_g","fd1_box"), ("hpc_2_g","fd2_box"), ("hpc_3_g","fd3_box")
    ])
    def test_hpc_inside_worker_subnet_y(self, cells, hpc_id, fd_id):
        wrk = cells["worker_sub"]
        hpc = cells[hpc_id]
        assert _contains_y(wrk, hpc, tol=TOLERANCE), (
            f"{hpc_id} y={hpc['y']:.0f} not inside Worker Subnet "
            f"[{wrk['y']:.0f}–{_bottom(wrk):.0f}]")

    @pytest.mark.parametrize("hpc_id,fd_id", [
        ("hpc_1_g","fd1_box"), ("hpc_2_g","fd2_box"), ("hpc_3_g","fd3_box")
    ])
    def test_hpc_centred_in_fd_column(self, cells, hpc_id, fd_id):
        fd  = cells[fd_id]
        hpc = cells[hpc_id]
        assert _centre_in_box(fd, hpc["x"]), (
            f"{hpc_id} centre {hpc['x']+24:.0f} not near {fd_id} centre {_cx(fd):.0f}")

    def test_operator_inside_op_subnet_y(self, cells):
        op  = cells["op_sub"]
        opr = cells["operator_1_g"]
        assert _contains_y(op, opr, tol=TOLERANCE)

    def test_operator_centred_in_fd2(self, cells):
        fd2 = cells["fd2_box"]
        opr = cells["operator_1_g"]
        assert _centre_in_box(fd2, opr["x"])

    def test_bastion_inside_bas_subnet_y(self, cells):
        bas = cells["bas_sub"]
        bst = cells["bastion_1_g"]
        assert _contains_y(bas, bst, tol=TOLERANCE)

    def test_bastion_centred_in_fd2(self, cells):
        fd2 = cells["fd2_box"]
        bst = cells["bastion_1_g"]
        assert _centre_in_box(fd2, bst["x"])


# ── 7. Instance Pool box contains all HPC nodes ──────────────────────────────

class TestInstancePool:
    def test_instance_pool_contains_hpc1(self, cells):
        ip   = cells["inst_pool_box"]
        hpc1 = cells["hpc_1_g"]
        assert _contains_x(ip, hpc1)
        assert _contains_y(ip, hpc1)

    def test_instance_pool_contains_hpc2(self, cells):
        ip   = cells["inst_pool_box"]
        hpc2 = cells["hpc_2_g"]
        assert _contains_x(ip, hpc2)
        assert _contains_y(ip, hpc2)

    def test_instance_pool_contains_hpc3(self, cells):
        ip   = cells["inst_pool_box"]
        hpc3 = cells["hpc_3_g"]
        assert _contains_x(ip, hpc3)
        assert _contains_y(ip, hpc3)


# ── 8. Gateways: inside region, right of VCN ─────────────────────────────────

class TestGatewayPlacement:
    GWS = ["nat_gw_g", "sgw_1_g", "igw_1_g"]

    @pytest.mark.parametrize("gid", GWS)
    def test_gateway_inside_region(self, cells, gid):
        reg = cells["region_box"]
        gw  = cells[gid]
        assert gw["x"] >= reg["x"] - TOLERANCE
        assert _right(gw) <= _right(reg) + TOLERANCE

    @pytest.mark.parametrize("gid", GWS)
    def test_gateway_right_of_vcn(self, cells, gid):
        vcn = cells["vcn_box"]
        gw  = cells[gid]
        assert gw["x"] >= _right(vcn) - TOLERANCE, (
            f"{gid} x={gw['x']:.0f} should be right of VCN edge {_right(vcn):.0f}")

    def test_nat_above_sgw(self, cells):
        assert cells["nat_gw_g"]["y"] < cells["sgw_1_g"]["y"]

    def test_sgw_above_igw(self, cells):
        assert cells["sgw_1_g"]["y"] < cells["igw_1_g"]["y"]

    def test_gateways_vertically_aligned(self, cells):
        """All gateways should share approximately the same x coordinate."""
        xs = [cells[g]["x"] for g in self.GWS]
        assert max(xs) - min(xs) <= TOLERANCE, f"Gateway x values not aligned: {xs}"


# ── 9. External services: outside region right edge ──────────────────────────

class TestExternalServices:
    SVCS = ["objstr_1_g", "monitor_1_g", "iam_1_g"]

    @pytest.mark.parametrize("sid", SVCS)
    def test_service_outside_region(self, cells, sid):
        reg = cells["region_box"]
        svc = cells[sid]
        assert svc["x"] >= _right(reg) - TOLERANCE, (
            f"{sid} x={svc['x']:.0f} should be outside region right edge "
            f"{_right(reg):.0f}")

    def test_services_vertically_ordered(self, cells):
        obj = cells["objstr_1_g"]
        mon = cells["monitor_1_g"]
        iam = cells["iam_1_g"]
        assert obj["y"] < mon["y"] < iam["y"]

    def test_services_horizontally_aligned(self, cells):
        xs = [cells[s]["x"] for s in self.SVCS]
        assert max(xs) - min(xs) <= TOLERANCE, f"Service x values not aligned: {xs}"


# ── 10. Gateway vertical alignment with subnets (matches PNG) ─────────────────

class TestGatewaySubnetAlignment:
    def test_nat_gw_aligned_with_cp_subnet(self, cells):
        """NAT GW should be at roughly the same vertical level as CP subnet."""
        cp  = cells["cp_sub"]
        nat = cells["nat_gw_g"]
        cp_mid  = _cy(cp)
        nat_mid = _cy(nat)
        assert abs(cp_mid - nat_mid) <= TOLERANCE * 2, (
            f"NAT GW cy={nat_mid:.0f} not near CP Subnet cy={cp_mid:.0f}")

    def test_igw_aligned_with_bastion_subnet(self, cells):
        """Internet GW should be at roughly the same vertical level as Bastion subnet."""
        bas = cells["bas_sub"]
        igw = cells["igw_1_g"]
        bas_mid = _cy(bas)
        igw_mid = _cy(igw)
        assert abs(bas_mid - igw_mid) <= TOLERANCE * 2, (
            f"Internet GW cy={igw_mid:.0f} not near Bastion Subnet cy={bas_mid:.0f}")


# ── 11. VCN / AD containment hierarchy ────────────────────────────────────────

class TestContainmentHierarchy:
    def test_vcn_inside_ad(self, cells):
        ad  = cells["ad1_box"]
        vcn = cells["vcn_box"]
        assert _contains_x(ad, vcn)
        assert _contains_y(ad, vcn)

    def test_ad_inside_region(self, cells):
        reg = cells["region_box"]
        ad  = cells["ad1_box"]
        assert _contains_x(reg, ad)
        assert _contains_y(reg, ad)

    def test_fds_inside_ad(self, cells):
        ad = cells["ad1_box"]
        for fd in ("fd1_box", "fd2_box", "fd3_box"):
            f = cells[fd]
            assert _contains_x(ad, f), f"{fd} x not inside AD"
            assert _contains_y(ad, f), f"{fd} y not inside AD"
