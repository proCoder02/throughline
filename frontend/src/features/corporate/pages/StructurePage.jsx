// Departments and teams: the tree on the left, the selected unit on the
// right (stacked on phones). The selected unit is in the URL (?unit=) so
// it survives a reload and can be linked to.
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import UnitTree from '../components/UnitTree.jsx';
import UnitPanel from '../components/UnitPanel.jsx';
import AddUnitForm from '../components/AddUnitForm.jsx';
import { PlusIcon, OrgTreeIcon } from '../../../icons.jsx';

export default function StructurePage({ corp }) {
  const [params, setParams] = useSearchParams();
  const { me, units } = corp.data;
  const [addingTop, setAddingTop] = useState(false);
  const selectedId = Number(params.get('unit')) || null;
  const selected = units.find((u) => u.id === selectedId) || null;
  const select = (id) => setParams(id ? { unit: String(id) } : {});

  return (
    <div className="corp-page wide">
      <header className="corp-page-head">
        <div>
          <h1>Structure</h1>
          <p className="corp-muted">Departments and the teams inside them. Each will get its own shared memory.</p>
        </div>
        {me.org_admin && !addingTop && (
          <button className="corp-btn primary" onClick={() => setAddingTop(true)}><PlusIcon aria-hidden="true" /> Department</button>
        )}
      </header>

      {addingTop && (
        <div className="corp-card">
          <AddUnitForm corp={corp} parent={null} onDone={(id) => { setAddingTop(false); select(id); }} onCancel={() => setAddingTop(false)} />
        </div>
      )}

      <div className={'corp-split' + (selected ? ' has-selection' : '')}>
        <div className="corp-card corp-tree-card">
          <UnitTree units={units} selectedId={selectedId} onSelect={select} />
        </div>
        {selected ? (
          <UnitPanel key={selected.id} corp={corp} unit={selected} onDeleted={() => select(null)} onSelect={select} />
        ) : (
          <div className="corp-card corp-panel-empty">
            <OrgTreeIcon className="corp-empty-icon" aria-hidden="true" />
            <p className="corp-muted">Pick a department or team to see who’s in it{me.org_admin ? ' and manage it' : ''}.</p>
          </div>
        )}
      </div>
    </div>
  );
}
