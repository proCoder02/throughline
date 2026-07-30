import { SearchIcon } from '../icons.jsx';

export default function ListPane({ title, headerAction, search, emptyText, fab, children }) {
  return (
    <div className="list-pane">
      <div className="list-header">
        <h1>{title}</h1>
        {headerAction}
      </div>
      {search && (
        <div className="search-box">
          <div className="search-box-wrap">
            <SearchIcon />
            <input placeholder={search.placeholder || 'Search'} value={search.value} onChange={(e) => search.onChange(e.target.value)} />
          </div>
        </div>
      )}
      <div className="list-scroll">
        {Array.isArray(children) && children.length === 0 ? (
          <div className="list-empty">{emptyText}</div>
        ) : children}
      </div>
      {fab}
    </div>
  );
}
