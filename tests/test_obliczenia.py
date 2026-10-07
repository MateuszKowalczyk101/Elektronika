import math
import random

import geiger_gui as g


def _poisson(lam, rng):
    limit, k, p = math.exp(-lam), 0, 1.0
    while p > limit:
        k += 1
        p *= rng.random()
    return k - 1


def test_chi2_sf_zgodne_z_tablicami():
    # wartosci krytyczne chi^2 dla poziomu 0,05
    assert abs(g._chi2_sf(3.841, 1) - 0.05) < 1e-3
    assert abs(g._chi2_sf(18.307, 10) - 0.05) < 1e-3
    assert abs(g._chi2_sf(124.342, 100) - 0.05) < 1e-3
    assert abs(g._chi2_sf(2.0, 10) - 0.99634) < 1e-4


def test_rozklad_poissona_sumuje_sie_do_1():
    assert abs(sum(g._poisson_pmf(k, 12.3) for k in range(100)) - 1.0) < 1e-12


def test_chi2_dla_prawdziwego_poissona_myli_sie_w_ok_5_procentach():
    rng = random.Random(1)
    ps = [g._poisson_chi2([_poisson(8.0, rng) for _ in range(200)])[2] for _ in range(300)]
    frac = sum(p < 0.05 for p in ps) / len(ps)
    assert 0.02 < frac < 0.09


def test_chi2_odrzuca_rozklad_szerszy_niz_poisson():
    rng = random.Random(2)
    values = [max(0, int(round(rng.gauss(8, 5)))) for _ in range(200)]
    assert g._poisson_chi2(values)[2] < 0.001


def test_chi2_za_malo_danych():
    assert g._poisson_chi2([5] * 9) is None


def test_dopasowanie_wazone_prostej():
    a, b, u_b, chi2 = g._weighted_line_fit([0, 1, 2, 3], [1, 3, 5, 7], [1, 1, 1, 1])
    assert (round(a, 12), round(b, 12), round(chi2, 12)) == (1.0, 2.0, 0.0)
    assert abs(u_b - math.sqrt(0.2)) < 1e-12


def test_czas_martwy_dwoch_zrodel_odtwarza_prawdziwe_tau():
    tau = 200e-6
    seen = lambda n: n / (1 + n * tau)   # licznik nieparalizujacy
    rb = 1.0
    r1, r2, r12, b = seen(3000 + rb), seen(2500 + rb), seen(5500 + rb), seen(rb)
    assert abs(g._two_source_tau(r1, r2, r12, b) - tau) < 1e-9
    t, u = g._two_source_tau_unc((r1, r2, r12, b), (7.0, 6.5, 9.5, 0.02))
    assert abs(t - tau) < 1e-9 and 0 < u < 50e-6


def test_czas_martwy_bez_strat_daje_zero():
    assert abs(g._two_source_tau(1000.0, 1000.0, 2000.0, 0.0)) < 1e-12


def test_format_wyniku_z_niepewnoscia():
    assert g._fmt_unc(156, math.sqrt(156), integer=True) == "156 ± 12"
    assert g._fmt_unc(0.312, 0.02498) == "0,312 ± 0,025"
    assert g._fmt_unc(3.588, 0.448) == "3,59 ± 0,45"
    assert g._fmt_unc(1234.7, 35.0) == "1235 ± 35"
