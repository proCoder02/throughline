// The organisation's departments and teams as a tree, built from the flat
// unit list (parent_id). Selecting a unit opens it in the panel beside.
import { OrgTreeIcon, FriendIcon } from '../../../icons.jsx';

export function buildTree(units) {
  const byParent = new Map();
  units.forEach((u) => {
    const key = u.parent_id ?? 0;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(u);
  });
  const sort = (list) => list.sort((a, b) =>
    (a.kind === b.kind ? 0 : a.kind === 'department' ? -1 : 1) || a.name.localeCompare(b.name));
  const walk = (parentKey) => sort(byParent.get(parentKey) || []).map((u) => ({ ...u, children: walk(u.id) }));
  return walk(0);
}

function Node({ node, selectedId, onSelect, depth }) {
  const Icon = node.kind === 'team' ? FriendIcon : OrgTreeIcon;
  return (
    <li role="treeitem" aria-selected={node.id === selectedId} aria-expanded={node.children.length ? true : undefined}>
      <button
        className={'corp-tree-row' + (node.id === selectedId ? ' active' : '')}
        style={{ paddingLeft: 10 + depth * 18 }}
        onClick={() => onSelect(node.id)}
      >
        <Icon className="corp-tree-icon" aria-hidden="true" />
        <span className="corp-tree-name">{node.name}</span>
        {node.my_role && <span className="corp-tag">{node.my_role === 'manager' ? 'You lead' : 'You'}</span>}
        <span className="corp-tree-count" title="People">{node.member_count}</span>
      </button>
      {node.children.length > 0 && (
        <ul role="group">
          {node.children.map((c) => <Node key={c.id} node={c} selectedId={selectedId} onSelect={onSelect} depth={depth + 1} />)}
        </ul>
      )}
    </li>
  );
}

export default function UnitTree({ units, selectedId, onSelect }) {
  const tree = buildTree(units);
  if (!tree.length) return <p className="corp-muted corp-pad">No departments yet.</p>;
  return (
    <ul className="corp-tree" role="tree" aria-label="Departments and teams">
      {tree.map((n) => <Node key={n.id} node={n} selectedId={selectedId} onSelect={onSelect} depth={0} />)}
    </ul>
  );
}
