#!/usr/bin/env python3
"""
Molecule property detection functions for routing decisions.

This module provides functions to detect various molecular properties
that can be used for routing prompts to different models.
"""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors


def get_molecule(smiles: str):
    """
    Get RDKit molecule object from SMILES string.

    Args:
        smiles: SMILES string

    Returns:
        RDKit Mol object or None if parsing fails
    """
    if not smiles or str(smiles).strip() == '':
        return None
    
    try:
        return Chem.MolFromSmiles(str(smiles).strip())
    except Exception:
        return None


def has_fused_ring_system(smiles: str, iupac: str = None, xml_metadata: str = None, **kwargs) -> bool:
    """
    Check if molecule contains fused ring systems.
    
    A fused ring system is one where rings share atoms (not just bonds).
    
    Args:
        smiles: SMILES string of the molecule
        iupac: IUPAC name (not used)
        xml_metadata: XML metadata (not used)
        **kwargs: Additional arguments (ignored)
    
    Returns:
        True if molecule has fused ring systems, False otherwise
    """
    mol = get_molecule(smiles)
    if mol is None:
        return False
    try:
        # Get ring information
        ring_info = mol.GetRingInfo()
        rings = ring_info.AtomRings()
        
        if len(rings) < 2:
            return False  # Need at least 2 rings for fusion
        
        # Check if any rings share atoms (fused)
        for i in range(len(rings)):
            for j in range(i + 1, len(rings)):
                ring1_atoms = set(rings[i])
                ring2_atoms = set(rings[j])
                # If rings share atoms, they are fused
                if ring1_atoms.intersection(ring2_atoms):
                    return True
        
        return False
    except Exception:
        return False

def has_spiro_ring(smiles: str, iupac: str = None, xml_metadata: str = None, **kwargs) -> bool:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return False

    return rdMolDescriptors.CalcNumSpiroAtoms(mol) > 0

def has_bridged_ring(smiles: str, iupac: str = None, xml_metadata: str = None, **kwargs) -> bool:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return False

    # Count bridgehead atoms
    n_bridgeheads = rdMolDescriptors.CalcNumBridgeheadAtoms(mol)

    return n_bridgeheads > 0


def count_rings(smiles: str, iupac: str = None, xml_metadata: str = None, **kwargs) -> int:
    """
    Count the number of rings in the molecule.
    
    Args:
        smiles: SMILES string of the molecule
        iupac: IUPAC name (not used)
        xml_metadata: XML metadata (not used)
        **kwargs: Additional arguments (ignored)
    
    Returns:
        Number of rings (0 if unavailable or error)
    """
    mol = get_molecule(smiles)
    if mol is None:
        return 0
    try:
        ring_info = mol.GetRingInfo()
        return len(ring_info.AtomRings())
    except Exception:
        return 0


def largest_fused_ring_count(smiles: str, iupac: str = None, xml_metadata: str = None, **kwargs) -> int:
    """
    Count the number of rings in the largest fused ring system.
    
    A fused ring system is a group of rings that share bonds with each other.
    This function finds all fused ring systems and returns the count of rings
    in the largest one.
    
    Args:
        smiles: SMILES string of the molecule
        iupac: IUPAC name (not used)
        xml_metadata: XML metadata (not used)
        **kwargs: Additional arguments (ignored)
    
    Returns:
        Number of rings in the largest fused ring system (0 if unavailable or error)
    """
    mol = get_molecule(smiles)
    if mol is None:
        return 0
    try:
        ring_info = mol.GetRingInfo()
        bond_rings = [set(r) for r in ring_info.BondRings()]
        n = len(bond_rings)
        if n == 0:
            return 0

        # Build adjacency: rings sharing ≥1 bond are fused
        adj = [[] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                if bond_rings[i] & bond_rings[j]:
                    adj[i].append(j)
                    adj[j].append(i)

        # Find connected components (fused systems)
        visited = [False] * n
        fused_sizes = []
        for i in range(n):
            if not visited[i]:
                stack = [i]
                visited[i] = True
                comp = []
                while stack:
                    u = stack.pop()
                    comp.append(u)
                    for v in adj[u]:
                        if not visited[v]:
                            visited[v] = True
                            stack.append(v)
                fused_sizes.append(len(comp))

        return max(fused_sizes) if fused_sizes else 0
    except Exception:
        return 0


def largest_fused_ring_has_heteroatom_in_each_subring(smiles: str, iupac: str = None, xml_metadata: str = None, **kwargs) -> bool:
    """
    Check if each subring in the largest fused ring system has at least one heteroatom.
    
    A heteroatom is any atom that is not carbon (atomic number 6) or hydrogen (atomic number 1).
    This function finds the largest fused ring system and checks if every ring in that system
    contains at least one heteroatom.
    
    Args:
        smiles: SMILES string of the molecule
        iupac: IUPAC name (not used)
        xml_metadata: XML metadata (not used)
        **kwargs: Additional arguments (ignored)
    
    Returns:
        True if all subrings in the largest fused system have at least one heteroatom,
        False otherwise (or if no fused systems exist)
    """
    mol = get_molecule(smiles)
    if mol is None:
        return False
    try:
        ring_info = mol.GetRingInfo()
        bond_rings = [set(r) for r in ring_info.BondRings()]
        atom_rings = ring_info.AtomRings()
        n = len(bond_rings)
        
        if n == 0:
            return False  # No rings at all

        # Build adjacency: rings sharing ≥1 bond are fused
        adj = [[] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                if bond_rings[i] & bond_rings[j]:
                    adj[i].append(j)
                    adj[j].append(i)

        # Find connected components (fused systems) and track the largest one
        visited = [False] * n
        largest_system = []
        max_size = 0
        
        for i in range(n):
            if not visited[i]:
                stack = [i]
                visited[i] = True
                comp = [i]
                while stack:
                    u = stack.pop()
                    for v in adj[u]:
                        if not visited[v]:
                            visited[v] = True
                            stack.append(v)
                            comp.append(v)
                
                if len(comp) > max_size:
                    max_size = len(comp)
                    largest_system = comp

        # A fused system requires at least 2 rings sharing bonds
        # If largest system has only 1 ring, it's not fused, so return False
        if max_size < 2 or len(largest_system) == 0:
            return False

        # Check each ring in the largest fused system for heteroatoms
        for ring_idx in largest_system:
            ring_bonds = bond_rings[ring_idx]
            ring_atoms = set()

            for bond_idx in ring_bonds:
                bond = mol.GetBondWithIdx(bond_idx)
                ring_atoms.add(bond.GetBeginAtomIdx())
                ring_atoms.add(bond.GetEndAtomIdx())

            if not any(
                mol.GetAtomWithIdx(a).GetAtomicNum() not in (1, 6)
                for a in ring_atoms
            ):
                return False
        
        # All rings have at least one heteroatom
        return True
    except Exception:
        return False

def count_fused_ring_systems(smiles: str, iupac: str = None, xml_metadata: str = None, **kwargs) -> int:
    """
    Count the number of distinct fused ring systems in the molecule.

    A fused ring system is defined as a connected group of
    two or more rings that share at least one bond.
    Isolated single rings are NOT counted.
    """
    mol = get_molecule(smiles)
    if mol is None:
        return 0

    try:
        ring_info = mol.GetRingInfo()
        bond_rings = [set(r) for r in ring_info.BondRings()]
        n = len(bond_rings)
        if n == 0:
            return 0

        # Build adjacency: rings sharing ≥1 bond
        adj = [[] for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                if bond_rings[i] & bond_rings[j]:
                    adj[i].append(j)
                    adj[j].append(i)

        visited = [False] * n
        fused_system_count = 0

        for i in range(n):
            if not visited[i]:
                stack = [i]
                visited[i] = True
                component_size = 1

                while stack:
                    u = stack.pop()
                    for v in adj[u]:
                        if not visited[v]:
                            visited[v] = True
                            stack.append(v)
                            component_size += 1

                # ✅ only count systems with ≥2 rings
                if component_size >= 2:
                    fused_system_count += 1

        return fused_system_count

    except Exception:
        return 0


def count_non_hydrogen_atoms(smiles: str, iupac: str = None, xml_metadata: str = None, **kwargs) -> int:
    """
    Count non-hydrogen atoms in the molecule.
    
    Args:
        smiles: SMILES string of the molecule
        iupac: IUPAC name (not used)
        xml_metadata: XML metadata (not used)
        **kwargs: Additional arguments (ignored)
    
    Returns:
        Number of non-hydrogen atoms (0 if unavailable or error)
    """
    mol = get_molecule(smiles)
    if mol is None:
        return 0
    
    try:
        return sum(1 for atom in mol.GetAtoms() if atom.GetAtomicNum() != 1)
    except Exception:
        return 0


def has_rs_configuration(smiles: str, iupac: str = None, xml_metadata: str = None, **kwargs) -> bool:
    """
    Check if molecule has R/S (chiral) configuration.
    
    Args:
        smiles: SMILES string of the molecule
        iupac: IUPAC name (not used)
        xml_metadata: XML metadata (not used)
        **kwargs: Additional arguments (ignored)
    
    Returns:
        True if molecule has R/S configuration, False otherwise
    """
    mol = get_molecule(smiles)
    if mol is None:
        return False
    
    try:
        # Check for chiral centers
        for atom in mol.GetAtoms():
            chiral_tag = atom.GetChiralTag()
            if chiral_tag in (Chem.ChiralType.CHI_TETRAHEDRAL_CW, Chem.ChiralType.CHI_TETRAHEDRAL_CCW):
                return True
        
        # Also check for @ in SMILES (stereochemistry indicators)
        if '@' in str(smiles):
            return True
        
        return False
    except Exception:
        return False


def get_difficulty_level(smiles: str, iupac: str = None, xml_metadata: str = None, **kwargs) -> str:
    """
    Classify molecule difficulty as "hard", "medium", or "easy" for routing.

    Logic follows MolLangData/scripts/routing_example.py route_molecule_and_prompt() (lines 68-82):
    - hard   -> gpt-5-pro, high   (largest_fused > 2 or (largest_fused > 1 and (spiro or bridged)) or num_fused_ring_systems > 1)
    - medium -> gpt-5-batch, high (largest_fused > 1)
    - easy   -> gpt-5-batch, medium (else)
    """
    # Same variables and conditions as routing_example.route_molecule_and_prompt
    has_spiro = has_spiro_ring(smiles, iupac=iupac, xml_metadata=xml_metadata, **kwargs)
    has_bridged = has_bridged_ring(smiles, iupac=iupac, xml_metadata=xml_metadata, **kwargs)
    largest_fused = largest_fused_ring_count(smiles, iupac=iupac, xml_metadata=xml_metadata, **kwargs)
    num_fused_ring_systems = count_fused_ring_systems(smiles, iupac=iupac, xml_metadata=xml_metadata, **kwargs)

    # Routing logic: prioritize complex systems (same as routing_example.py)
    if largest_fused > 2 or (largest_fused > 1 and (has_spiro or has_bridged)) or (num_fused_ring_systems > 1):
        return "hard"
    elif largest_fused > 1:
        return "medium"
    else:
        return "easy"


# Registry of available property functions
PROPERTY_REGISTRY = {
    'has_fused_ring_system': has_fused_ring_system,
    'has_fused_rings': has_fused_ring_system,  # Alias
    'has_spiro_ring': has_spiro_ring,
    'has_spiro': has_spiro_ring,  # Alias
    'has_bridged_ring': has_bridged_ring,
    'has_bridged': has_bridged_ring,  # Alias
    'count_rings': count_rings,
    'num_rings': count_rings,  # Alias
    'largest_fused_ring_count': largest_fused_ring_count,
    'max_fused_rings': largest_fused_ring_count,  # Alias
    'largest_fused_ring_has_heteroatom_in_each_subring': largest_fused_ring_has_heteroatom_in_each_subring,
    'largest_fused_has_heteroatoms': largest_fused_ring_has_heteroatom_in_each_subring,  # Alias
    'count_fused_ring_systems': count_fused_ring_systems,
    'num_fused_ring_systems': count_fused_ring_systems,  # Alias
    'count_non_hydrogen_atoms': count_non_hydrogen_atoms,
    'num_atoms': count_non_hydrogen_atoms,  # Alias
    'has_rs_configuration': has_rs_configuration,
    'has_rs': has_rs_configuration,  # Alias
}


def get_property_function(property_name: str):
    """
    Get a property detection function by name.
    
    Args:
        property_name: Name of the property function
    
    Returns:
        The property function, or None if not found
    """
    return PROPERTY_REGISTRY.get(property_name.lower())


def list_available_properties():
    """Return a list of available property function names."""
    return list(PROPERTY_REGISTRY.keys())

if __name__ == "__main__":
    smiles = "CC(C)(C)c1ccc(C(C)(C)N(c2ccccc2)c2ccc3ccccc3c2)cc1"
    print(largest_fused_ring_count(smiles))
    print(has_spiro_ring(smiles))
    print(has_bridged_ring(smiles))