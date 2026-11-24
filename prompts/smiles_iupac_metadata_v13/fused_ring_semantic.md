## **Fused Ring Semantics (XML)**

A fused ring system is represented in the XML by **incrementally constructing new ring identities** from simpler rings.
Each fusion step introduces a **new global labeling scheme**, which replaces the previous ones and is used for any subsequent fusion and structural references.

### **Core semantics**

1. **Local ring definition**

  * Each ring is described by a `value` attribute containing its **SMILES representation**; in some fused ring systems, this SMILES value may be omitted.
  * A `labels` attribute assigns **atom labels** for the ring; when a SMILES value is present, the labels follow the **atom order in the SMILES**.
    * If `labels` is **explicitly provided**, those labels define the atom labeling scheme.
    * If `labels` is **omitted** or set to **`numeric`**, the atoms are implicitly labeled **from 1 to *n***, where *n* is the number of atoms in the ring, **following the atom order in the SMILES**.
  * Atom indices **start from 1**.


2. **Fusion via `originalLabels`**

   * `originalLabels` maps **each atom of the newly fused system** to atom indices in the component rings.
   * Each entry corresponds to **one atom in the fused system**.
   * Multiple indices in an entry indicate a **fusion point**.
   * A blank position indicates that the fused-system atom does not belong to that ring.

3. **Label propagation**

   * After fusion, the fused system receives a **new `labels` list**.
   * This new labeling scheme **replaces all previous local labels**.
   * All subsequent structural references — including further fusions, bridged connections, spiro connections, substituent attachment positions, and stereochemical descriptors — **must reference this new labeling scheme**, not the original local labels of the component rings.


### **Worked Example: indeno[5,6-b]furan**

```xml
<group type="ring" subType="fusedRing" value="indeno[5,6-b]furan">
  <fusedChildRing
      type="ring"
      subType="ring"
      value="o1cccc1"
      labels="1/2/3/4/5">
    furan
  </fusedChildRing>
  <fusedChildRing
      type="ring"
      subType="fusionRing"
      value="[cH2]1ccc2ccccc12"
      labels="1/2/3/3a/4/5/6/7/7a"
      fusedRing1="[cH2]1cccc1"
      fusedRing2="c1ccccc1"
      originalLabels="(1,)/(2,)/(3,)/(4,1)/(,2)/(,3)/(,4)/(,5)/(5,6)">
    indeno
  </fusedChildRing>
  <fusedRingLabels
      labels="1/2/3/3a/4/4a/5/6/7/7a/8/8a"
      originalLabels="(1, )/(5, )/(4, )/(3, 6)/(, 7)/(, 7a)/(, 1)/(, 2)/(, 3)/(, 3a)/(, 4)/(2, 5)">
  </fusedRingLabels>
</group>
```

### **Step-by-step interpretation**

#### **Step 1: Ring A (furan)**

* SMILES: `o1cccc1`
* Labels: `1/2/3/4/5`
* Label assignment follows SMILES order:

  * O → 1
  * Carbons → 2–5

#### **Step 2: Ring B (indeno)**

Ring B is formed by fusing two sub-rings:

* `fusedRing1`: `[cH2]1cccc1` → atoms **1–5**
* `fusedRing2`: `c1ccccc1` → atoms **1–6**

Fusion is defined by:

```
originalLabels = (1,)/(2,)/(3,)/(4,1)/(,2)/(,3)/(,4)/(,5)/(5,6)
```

Fusion points:

* atom **4** of `fusedRing1` ↔ atom **1** of `fusedRing2`
* atom **5** of `fusedRing1` ↔ atom **6** of `fusedRing2`

After fusion, Ring B receives a **new labeling scheme**:

```
labels = 1/2/3/3a/4/5/6/7/7a
```

From this point onward, **indeno is treated as a single ring**.

#### **Step 3: Fuse Ring A with Ring B**

The final fused system (indeno[5,6-b]furan) is created by fusing:

* Ring A (labels `1/2/3/4/5`)
* Ring B (labels `1/2/3/3a/4/5/6/7/7a`)

The final system receives a **new global labeling scheme**:

```
labels = 1/2/3/3a/4/4a/5/6/7/7a/8/8a
```

The fusion is defined by:

```
originalLabels =
(1, )/(5, )/(4, )/(3, 6)/(, 7)/(, 7a)/(, 1)/(, 2)/(, 3)/(, 3a)/(, 4)/(2, 5)
```

Fusion points:

* atom **3 of Ring A** ↔ atom **6 of Ring B**
* atom **2 of Ring A** ↔ atom **5 of Ring B**
