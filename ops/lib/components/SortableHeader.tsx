export function SortableHeader<T extends string>({
  column,
  label,
  activeColumn,
  direction,
  onSort,
}: {
  column: T;
  label: string;
  activeColumn: T;
  direction: "asc" | "desc";
  onSort: (column: T) => void;
}) {
  const active = column === activeColumn;
  return (
    <th>
      <button type="button" className="ops-th-sort" data-active={active} onClick={() => onSort(column)}>
        {label}
        {active && <span className="ops-sort-glyph" aria-hidden="true">{direction === "asc" ? "▲" : "▼"}</span>}
      </button>
    </th>
  );
}
