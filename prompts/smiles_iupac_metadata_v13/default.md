**Task:**
You are provided with the **IUPAC name**, **SMILES string**, and **rule-based hierarchical metadata** describing the molecular structure.
Generate a **detailed and accurate structural description** that would allow a person with **basic organic chemistry knowledge** to reconstruct the exact molecule using only your structural description text.
The description should be **natural**, **diverse**, and **chemically precise**, conveying clear information about the molecular skeleton, substituents, and stereochemistry.

In addition, after completing the description, report the total number of non-hydrogen atoms implied by your description, computed **only from the information explicitly stated in the description itself**.

**Guidelines:**
1. **Purpose and Independence**

   * The description must be **self-contained** and **sufficient for reconstruction**.
   * Assume the reader only has your final text — they will **not see** the SMILES, IUPAC name, or metadata.
   * You may use all input data internally to reason about the molecule, but the final description must read as a stand-alone, human-readable explanation.

2. **Freedom of Description**
   You may begin from any perspective --- the main skeleton, a key ring system, or an important substituent.
   Combine information freely from the IUPAC name, SMILES, and metadata to capture the complete structure.

3. **Backbone and Connectivity**
   Describe how rings, chains, and substituents are connected.
   Indicate branching positions, linkages, and overall topology so that the structure can be reconstructed accurately.

4. **Functional Groups and Substituents**
   Identify all key functional groups and substituents.
   Specify their type (e.g., hydroxyl, amine, halogen, carbonyl), location, and bonding pattern relative to the molecular framework.

5. **Simple or Isolated Rings**
   For common rings (benzene, pyridine, cyclohexane), you may name them directly or briefly describe their composition and bonding.

6. **Fused, Bridged, or Spiro Ring Systems — explicit, structured, and verified**
   It is **strongly recommended to follow the guidance below** when describing complex ring topologies:
   
   When interpreting fused, bridged, or spiro ring systems, you should explicitly refer to the corresponding ring-system semantics described later. These semantics are essential for correctly understanding atom labeling, fusion points, bridge connections, and spiro locants as derived from the provided metadata.

   * **(a) Define and label atoms/rings.**
     Explicitly assign ring labels and atom labels to each ring in the ring system (e.g., "Ring A: C1–C6 clockwise starting at the junction with ring B"; include heteroatoms like O1/N5 as needed).
     For complex fused or multi-ring systems, it is recommended to align the atom labeling by the structure metadata to maintain chemical accuracy.
     If there are multiple distinct fused systems, assign separate, non-overlapping label sets to avoid confusion.
   * **(b) Describe internal ring features.**
     State ring size, aromaticity/saturation, heteroatoms, and bonding patterns (alternation, single/double).
   * **(c) Explain how rings are connected.**
     Specify **exact shared atoms or edges** (e.g., "Ring B shares the C5–C6 edge with ring A").
     Describe **fusion geometry** (linear, angular, bridged, spiro).

   **Required verification (before finalizing your description):**

   * **Label consistency:** Each label you introduced is used consistently; no duplicate or skipped numbering within a ring.
   * **Ring labeling check:** Verify that the atom labeling and numbering sequence are correct, and that the shared atoms or edges described for each fusion correspond accurately to your own ring definitions and orientations. The metadata should be treated as the **gold standard** for validating both labeling and fusion topology.
   * **Orientation sanity-check:** The shared atoms/edge and the named orientation (linear vs angular; inner vs outer edge) match the topology. Do not accidentally mirror or rotate the fusion.
   * **Cross-consistency check:** Ensure that substituent positions and stereochemical designations correspond correctly to the atom labels and numbering scheme you defined for each ring.

7. **Stereochemistry**
   Include stereochemical information such as (R/S) or (E/Z) when available, and describe how these configurations relate to surrounding atoms or bonds.

8. **Rational Use of Metadata**

   * Treat the metadata as **accurate structural evidence**, but express its meaning in **your own words**.
   * The metadata is not shown to the reader, so do not include or reference any of its raw contents directly.
   * Only mention atoms, labels, or locants (e.g., "C9," "N5") **after you have introduced them** in your own description (for example: "Label the carbons in the first ring as C1–C6"). **Do not quote locants or atom labels from metadata directly** unless you have already introduced that contextually.
   * When metadata provides **partial or shorthand notations** (e.g., "–NH–," "–CO–"), **infer and expand** these to their complete, chemically correct forms (e.g., "–NH₂," "carbonyl group," "amide linkage") based on structural reasoning.

9. **Use of Chemical Shorthand**

   * **Avoid full structural formulas** written as continuous symbolic notations (e.g., "HOOC–CH2–N(CH3)–C(=O)–NH").
   * However, it is **acceptable to use short chemical fragments** like "–OH," "–CH3," or "–NH2" when they make the text clearer or more concise.
   * Focus on word-based descriptions for larger connectivity, using shorthand only for small groups.

10. **Balance and Readability**
   Aim for a **balanced level of detail** --- avoid unnecessary repetition or verbosity.
   It's fine to be concise when the structure is simple, and more elaborate when it is complex.

11. **Descriptive Diversity**
   Use varied styles and sentence structures.
   The aim is to build a rich, diverse dataset of descriptions that remain chemically accurate.

12. **Do Not Include**

   * The full IUPAC name, SMILES string, or XML tags verbatim.
   * Long symbolic chemical formulas for the entire molecule.
   * Brand names, trivial comments, or unrelated metadata.
   * Unintroduced atom labels or locants.

13. **Non-hydrogen atom count**

   * After completing the description, report the **total number of non-hydrogen atoms** based only on the structure described in your text.
   * Do **not** use the SMILES to perform this count.
   * Do **not** include the counting process in your description.

**Output Format:**
Place your description between <description> and </description> tags.
```xml
<description>
[Concise, varied, and chemically precise structural description]
</description>
<non_hydrogen_atom_count>
[integer]
</non_hydrogen_atom_count>
```

**Inputs:**
* **IUPAC Name:** `{IUPAC}`
* **SMILES String:** `{SMILES}`
* **Molecular Metadata (XML Hierarchy):**
```xml
{XML_METADATA}
```
