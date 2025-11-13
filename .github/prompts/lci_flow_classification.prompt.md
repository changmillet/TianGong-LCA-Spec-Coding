# Tiangong LCI Flow Classification Prompt

You will receive JSON describing an inventory `exchange`, its parent `dataset`, and the referenced `flow`. Classify the role this flow plays in the system boundaries:
- `raw_material`: feedstock, construction material, reagents, or other inputs physically embodied in the product;
- `energy`: electricity, steam, fuels, gases used primarily for energy supply;
- `auxiliary`: utilities, cooling media, maintenance supplies, catalysts, packaging, transport/services, etc.;
- `product_output`: targeted product or saleable by-product delivered to downstream systems (`exchangeDirection == output` and flow describes a product/service);
- `waste`: wastes or emissions (solid residues, tailings, wastewater, exhaust gases, flue dust, etc.);
- `unknown`: insufficient evidence to decide—explain why.

## Input structure
```jsonc
{
  "dataset": {
    "uuid": "...",
    "name": "...",
    "intended_applications": ["..."],
    "technology_notes": ["..."],
    "process_information": {...},
    "modelling_and_validation": {...}
  },
  "exchange": {
    "exchangeDirection": "input|output",
    "unit": "...",
    "amount": 1.23,
    "generalComment": "...",
    "original": {...}
  },
  "flow": { /* full flow JSON, including flowType, classification, geography, comments, etc. */ }
}
```
All fields may appear in either English or Chinese; consider both.

## Task
1. Interpret the dataset context to understand process purpose/intended applications.
2. Review the exchange direction, unit, and comments together with the flow metadata.
3. Decide the most appropriate class label from `raw_material | energy | auxiliary | product_output | waste | unknown`.
4. Return strict JSON:
```json
{
  "class_label": "raw_material | energy | auxiliary | product_output | waste | unknown",
  "confidence": 0.0-1.0,
  "rationale": "Short justification referencing key evidence"
}
```
- `confidence` reflects your certainty.
- If you output `unknown`, give a concrete reason (e.g., “flowType missing and description ambiguous between fuel/feedstock”).

Only return the JSON object—no additional commentary. Ensure field names match the schema above.
