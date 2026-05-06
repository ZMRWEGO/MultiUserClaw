## ADDED Requirements

### Requirement: Workspace panel uses horizontal left-right layout
The system SHALL render the right workspace panel as a horizontal left-right layout: the file tree on the left and the file preview on the right.

#### Scenario: Workspace panel is open
- **WHEN** the workspace panel is expanded
- **THEN** the file tree is displayed on the left side of the panel
- **AND** the file preview is displayed on the right side of the panel
- **AND** both areas share the full height of the workspace panel

### Requirement: Workspace panel supports horizontal resize between file tree and preview
The system SHALL provide a draggable resize handle between the file tree and the file preview areas, allowing users to adjust their relative widths.

#### Scenario: Dragging the horizontal resize handle
- **WHEN** user drags the resize handle between the file tree and preview areas left or right
- **THEN** the widths of the file tree and preview areas adjust in real time
- **AND** the total width of the workspace panel remains unchanged
- **AND** neither area can be resized below a minimum width of 120px

### Requirement: Main layout supports resize between chat area and workspace panel
The system SHALL provide a draggable resize handle between the middle chat area and the right workspace panel, allowing users to adjust the workspace panel width.

#### Scenario: Dragging the workspace panel edge
- **WHEN** user drags the resize handle between the chat area and the workspace panel
- **THEN** the workspace panel width adjusts in real time
- **AND** the chat area width adjusts accordingly
- **AND** the workspace panel cannot be resized below 300px when expanded

### Requirement: Workspace panel can be fully collapsed
The system SHALL allow users to fully collapse the workspace panel via a toggle button, and restore it via the same button.

#### Scenario: Toggling workspace panel
- **WHEN** user clicks the workspace panel toggle button
- **THEN** if the panel is expanded, it collapses completely
- **AND** if the panel is collapsed, it restores to its previous width
