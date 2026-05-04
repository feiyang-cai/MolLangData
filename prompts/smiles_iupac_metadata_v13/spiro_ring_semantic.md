## **Spiro Ring Semantics (XML)**

For a spiro ring system, the XML normally provides:

* One or more **`spiroSystemComponent`** entries, each describing a **complete ring system**

  * Each component has a `value` (SMILES) and a `labels` attribute
  * If `labels` is **explicitly provided**, those labels are used; if `labels` is **omitted** or set to **`numeric`**, the atoms are implicitly labeled **from 1 to *n***, following the **SMILES atom order**
  * A component may itself be a fused ring system, following the fused-ring semantics
* A **`spiroLocant`**, which specifies the **spiro atom in each ring system**

Each ring system **retains its own labeling scheme**.
To distinguish atoms belonging to different ring systems, **prime notation** (`'`, `''`, …) is used.

The locants in `spiroLocant` are listed in the **same order as the `spiroSystemComponent` entries** and identify the **single atom in each ring system** that represents the same physical atom.

### **Worked Example: spiro[cyclopentane-1,1′-indene]**

```xml
<group type="spiro system" subType="Non-Identical Polycyclic" value="spiro, pent, inden">
  <spiroSystemComponent type="ring" subType="alkaneStem" value="C1CCCC1" labels="numeric">
    pent
  </spiroSystemComponent>
  <spiroLocant>1,1'</spiroLocant>
  <spiroSystemComponent
      type="ring"
      subType="ring"
      value="[cH2]1ccc2ccccc12"
      labels="1/2/3/3a/4/5/6/7/7a"
      fusedRing1="[cH2]1cccc1"
      fusedRing2="c1ccccc1"
      originalLabels="(1,)/(2,)/(3,)/(4,1)/(,2)/(,3)/(,4)/(,5)/(5,6)">
    inden
  </spiroSystemComponent>
</group>
```

Interpretation:

* **Cyclopentane** uses labels `1–5`; spiro atom is **1**
* **Indene** uses its own labeling scheme; spiro atom is **1′**
* Atoms **1** and **1′** refer to the **same physical atom**

All other atoms are referenced using their **component-specific labels with primes**, consistent with the XML.
