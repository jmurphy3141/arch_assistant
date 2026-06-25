# C3E JEP Word Template

This directory holds the runtime Word template used to render generated JEPs
into the approved C3E format.

Expected template filename:

```text
c3e_jep_template.docx
```

Template requirements:

- Sanitized and approved for repository storage.
- No customer-specific data, secrets, or confidential notes.
- Includes the desired C3E page setup, headers, footers, fonts, heading styles,
  list styles, and table styles.
- Uses normal Word styles where possible, including `Title`, `Heading 1`,
  `Heading 2`, `Normal`, list styles, and table styling.

The JEP DOCX renderer should treat this file as the formatting source of truth.
