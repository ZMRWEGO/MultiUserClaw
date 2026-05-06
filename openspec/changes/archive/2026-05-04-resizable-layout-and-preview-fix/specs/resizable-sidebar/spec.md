## ADDED Requirements

### Requirement: Left sidebar can be collapsed and expanded
The system SHALL provide a toggle button on the left sidebar that allows users to collapse the sidebar to a narrow strip and expand it back to full width.

#### Scenario: Collapsing the sidebar
- **WHEN** user clicks the collapse toggle button on the left sidebar
- **THEN** the sidebar width reduces to a narrow strip (showing only the new-chat icon button)
- **AND** the middle chat area expands to occupy the freed space

#### Scenario: Expanding the sidebar
- **WHEN** user clicks the expand toggle button on the collapsed sidebar
- **THEN** the sidebar restores to its full width (w-64 equivalent)
- **AND** the middle chat area shrinks accordingly

### Requirement: Sidebar collapse state persists across sessions
The system SHALL persist the user's sidebar collapse state in localStorage so it is restored on page reload.

#### Scenario: State persistence after reload
- **WHEN** user collapses the sidebar and then reloads the page
- **THEN** the sidebar initializes in the collapsed state
- **AND** when user expands the sidebar and reloads the page
- **THEN** the sidebar initializes in the expanded state

### Requirement: No SSR hydration mismatch for sidebar state
The system SHALL read the persisted collapse state only on the client side after hydration to avoid React hydration mismatches.

#### Scenario: Server-rendered page loads
- **WHEN** the page is server-rendered
- **THEN** the sidebar initially renders in the expanded state
- **AND** after client hydration, if localStorage indicates collapsed, it transitions to collapsed
