## ADDED Requirements

### Requirement: Excel preview displays header in thead with sticky positioning
The system SHALL render Excel spreadsheets with the header row placed inside a `<thead>` element and fixed at the top of the scrollable container using `position: sticky`.

#### Scenario: Small Excel file preview
- **WHEN** user selects an Excel file with <= 200 rows and <= 50 columns
- **THEN** the preview renders as a scrollable HTML table
- **AND** the first row is rendered inside `<thead>` with sticky positioning
- **AND** when scrolling vertically, the header row remains visible at the top

#### Scenario: Excel preview does not overflow container
- **WHEN** user selects any Excel file
- **THEN** the preview area does not expand beyond its parent container
- **AND** horizontal and vertical scrolling is contained within the preview panel

### Requirement: PDF preview scales to fit container
The system SHALL render PDF previews inside an iframe that scales to 100% width and 100% height of its parent container without overflowing.

#### Scenario: PDF file preview
- **WHEN** user selects a PDF file
- **THEN** the PDF renders inside an iframe that fills the entire preview panel
- **AND** the iframe does not cause the preview panel or outer layout to expand beyond viewport boundaries

### Requirement: Word preview adapts to container width
The system SHALL render Word document previews so that the content width does not exceed the container width, with scrolling handled inside the preview area.

#### Scenario: Word file preview
- **WHEN** user selects a .docx file
- **THEN** the docx-preview rendered content fits within the preview panel width
- **AND** if the document content is wider than the panel, horizontal scrolling is contained inside the preview area
- **AND** the outer layout is not pushed wider by the document content

### Requirement: HTML preview iframe fills container
The system SHALL render HTML previews inside a sandboxed iframe that fills 100% width and 100% height of the preview panel.

#### Scenario: HTML file preview
- **WHEN** user selects an HTML file
- **THEN** the iframe occupies the full available space in the preview panel
- **AND** the iframe does not overflow its container

### Requirement: All preview components contain overflow internally
The system SHALL ensure that all preview components handle content overflow internally via scrolling, never expanding the parent flex container.

#### Scenario: Large content preview
- **WHEN** user selects any file with content larger than the preview panel
- **THEN** the content is clipped to the panel bounds
- **AND** scrolling is available inside the preview component
- **AND** the outer page layout remains stable without horizontal expansion
