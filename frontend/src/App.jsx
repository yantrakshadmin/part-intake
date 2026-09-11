import { useHashRoute } from './lib/router.js'
import LoadCalculator from './components/LoadCalculator.jsx'
import NewProject from './components/NewProject.jsx'
import ProjectsList from './components/ProjectsList.jsx'
import ProjectPage from './components/ProjectPage.jsx'
import Assets from './components/Assets.jsx'

const NAV = [
  { hash: '#/projects', label: 'Projects', match: (r) => r.page === 'projects' || r.page === 'project' },
  { hash: '#/projects/new', label: 'New project', primary: true, match: (r) => r.page === 'new-project' },
  { hash: '#/assets', label: 'Assets', match: (r) => r.page === 'assets' },
]

/** App shell (F2): persistent left nav + router-driven main area. Replaces
 *  the old top segmented control and its `page` state. */
export default function App() {
  const route = useHashRoute()

  return (
    <div className="shell">
      <nav className="side-nav">
        <div className="brand">
          <div className="brand-mark">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinejoin="round">
              <path d="M12 2 3 7v10l9 5 9-5V7l-9-5z" />
              <path d="M3 7l9 5 9-5M12 12v10" />
            </svg>
          </div>
          <div>
            <h1>Part Intake</h1>
            <p>Register parts for packaging &amp; insert design</p>
          </div>
        </div>

        <div className="side-nav-links">
          {NAV.map((n) => (
            <a key={n.hash} href={n.hash}
              className={`side-nav-link${n.primary ? ' primary' : ''}${n.match(route) ? ' active' : ''}`}>
              {n.label}
            </a>
          ))}
        </div>

        <div className="side-nav-tools">
          <div className="side-nav-group-label">Tools</div>
          <a href="#/tools/load-calculator"
            className={`side-nav-link${route.page === 'load-calculator' ? ' active' : ''}`}>
            Load calculator
          </a>
        </div>
      </nav>

      <main className="side-nav-main">
        {route.page === 'projects' && <ProjectsList />}
        {route.page === 'new-project' && <NewProject />}
        {route.page === 'project' && <ProjectPage id={route.id} tab={route.tab} query={route.query} />}
        {route.page === 'assets' && <Assets />}
        {route.page === 'load-calculator' && <LoadCalculator />}
      </main>
    </div>
  )
}
