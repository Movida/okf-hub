#!/usr/bin/env python3
"""Test du mécanisme de profils dans hub-config.yaml."""

from pathlib import Path
import sys
import tempfile

# Ajouter le src du hub au PYTHONPATH
sys.path.insert(0, str(Path("/home/vscode/okf-hub-v3/okf-hub/src").resolve()))

from okf_hub.config import HubConfig

def test_profil_solo_par_defaut():
    """Sans config, le profil solo est utilisé par défaut."""
    with tempfile.TemporaryDirectory() as tmp:
        hub_root = Path(tmp)
        cfg = HubConfig.load(hub_root)
        assert cfg.bases_dir == hub_root / 'bases'
        assert cfg.read_toc_threshold == 8192
        assert cfg.bootstrap_bundles is True
        assert cfg.sync_on_start is True
    print('✓ Test 1: profil solo par défaut (pas de config)')

def test_profil_solo_explicite():
    """Avec profile: solo, comportement identique au défaut."""
    with tempfile.TemporaryDirectory() as tmp:
        hub_root = Path(tmp)
        (hub_root / 'hub-config.yaml').write_text('profile: solo', encoding='utf-8')
        cfg = HubConfig.load(hub_root)
        assert cfg.bases_dir == hub_root / 'bases'
        assert cfg.read_toc_threshold == 8192
        assert cfg.bootstrap_bundles is True
        assert cfg.sync_on_start is True
    print('✓ Test 2: profil solo explicite')

def test_profil_dev():
    """Profil dev : pas de bootstrap ni sync auto."""
    with tempfile.TemporaryDirectory() as tmp:
        hub_root = Path(tmp)
        (hub_root / 'hub-config.yaml').write_text('profile: dev', encoding='utf-8')
        cfg = HubConfig.load(hub_root)
        assert cfg.bootstrap_bundles is False
        assert cfg.sync_on_start is False
        assert cfg.read_toc_threshold == 8192  # reste par défaut
    print('✓ Test 3: profil dev')

def test_profil_ci():
    """Profil ci : seuil plus élevé, pas de log fichier."""
    with tempfile.TemporaryDirectory() as tmp:
        hub_root = Path(tmp)
        (hub_root / 'hub-config.yaml').write_text('profile: ci', encoding='utf-8')
        cfg = HubConfig.load(hub_root)
        assert cfg.read_toc_threshold == 16384
        assert cfg.log_file is None
        assert cfg.bootstrap_bundles is True  # reste par défaut
    print('✓ Test 4: profil ci')

def test_profil_avec_surcharge():
    """Les valeurs explicites de hub-config.yaml surchargent le profil."""
    with tempfile.TemporaryDirectory() as tmp:
        hub_root = Path(tmp)
        config = """profile: dev
read-toc-threshold: 4096
"""
        (hub_root / 'hub-config.yaml').write_text(config, encoding='utf-8')
        cfg = HubConfig.load(hub_root)
        assert cfg.bootstrap_bundles is False  # du profil dev
        assert cfg.read_toc_threshold == 4096  # surchargé
    print('✓ Test 5: profil dev + surcharge read-toc-threshold')

def test_profil_inconnu():
    """Un profil inconnu doit être rejeté."""
    with tempfile.TemporaryDirectory() as tmp:
        hub_root = Path(tmp)
        (hub_root / 'hub-config.yaml').write_text('profile: inexistant', encoding='utf-8')
        try:
            HubConfig.load(hub_root)
            assert False, 'devrait échouer'
        except ValueError as e:
            assert 'profil inconnu' in str(e).lower()
    print('✓ Test 6: profil inconnu rejeté correctement')

if __name__ == '__main__':
    test_profil_solo_par_defaut()
    test_profil_solo_explicite()
    test_profil_dev()
    test_profil_ci()
    test_profil_avec_surcharge()
    test_profil_inconnu()
    print('\n✓ Tous les tests de profils passent!')
