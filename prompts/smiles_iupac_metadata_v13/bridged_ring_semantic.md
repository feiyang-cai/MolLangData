## **Bridged Ring Semantics (XML)**

A bridged ring system is represented by defining a **parent ring system** and a **bridge fragment** that connects two atoms of the parent ring.

For a bridged ring system, the XML normally provides:

* A **parent ring system**, defined by its `value` (SMILES, when provided) and `labels`
  (the parent ring may itself be a fused ring system, following the fused-ring semantics)
* A **bridge fragment**, defined by its own `value` (SMILES) and `labels`
* A **`bridgeLocants`** attribute, which specifies the **two atom labels of the parent ring** that are connected by the bridge

The `bridgeLocants` are ordered and define the **direction along the bridge**.

The labels of the bridge fragment are assigned **from the first locant to the second locant**, following the order of the bridge fragment's SMILES.

### **Worked Example: 4a,8a-propanoquinoline**

```xml
<group type="ring" subType="bridgeSystem" value="propanoquinoline">
  <bridgeParent
      type="ring"
      subType="ring"
      value="n1cccc2ccccc12"
      labels="1/2/3/4/4a/5/6/7/8/8a"
      fusedRing1="n1ccccc1"
      fusedRing2="c1ccccc1"
      originalLabels="(1,)/(2,)/(3,)/(4,)/(5,1)/(,2)/(,3)/(,4)/(,5)/(6,6)">
    quinoline
  </bridgeParent>
  <bridgeChild
      type="chain"
      subType="alkaneStem"
      value="-CCC-"
      labels="11/10/9"
      usableAsAJoiner="yes"
      bridgeLocants="4a,8a">
    prop
  </bridgeChild>
</group>
```

Interpretation:

* Parent ring labels: `1/2/3/4/4a/5/6/7/8/8a`
* Bridge locants: `4a,8a`
* Bridge labels: `11/10/9`

The bridge is incorporated directionally as:

> **4a – 11 – 10 – 9 – 8a**

The resulting structure uses a **single combined labeling scheme**, consisting of the parent-ring labels extended by the bridge labels.
All subsequent references (further fusions, substituents, stereochemistry) must use this combined labeling scheme.
