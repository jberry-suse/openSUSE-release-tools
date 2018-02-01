from osclib.core import project_list_prefix

def project_list_family(apiurl, project):
    count_original = project.count(':')
    if project.startswith('SUSE:SLE'):
    #if project.endswith(':GA'):
        project = ':'.join(project.split(':')[:2])
        #print(project)
        #project = project[:-3]
        #f = lambda p: p.endswith(':Update') or p.endswith(':GA')
        #f = lambda p: p.endswith(':GA')
        f = lambda p: p.endswith(':GA') and not p.startswith('SUSE:SLE-11')
    else:
        #f = lambda p: p.count(':') == count_original or p.endswith(':Update')
        f = lambda p: p.count(':') == count_original

    prefix = ':'.join(project.split(':')[:-1])
    projects = project_list_prefix(apiurl, prefix)

    return filter(f, projects)

    #count_original = project.count(':')
    #print(project[-3:-1])
    #return filter(lambda p: p.endswith(':Update') or p.endswith(':GA'), projects)
    #return filter(lambda p: (p.endswith(':Update') or p.endswith(':GA')) or (p.count(':') == 3 and p[-3:-1] == 'SP'), projects)
    #return filter(lambda p: p.count(':') == count_original, projects)

def project_list_family_sorter(project):
    version = project_version(project)

    if version >= 42:
        version -= 42

    if project.endswith(':Update'):
        version += 0.01

    #print(project, version)
    return version

def project_version(project):
    if ':Leap:' in project:
        #print(project, float(project.split(':')[2]))
        return float(project.split(':')[2])

    if ':SLE-' in project:
        # SLE-15 or SLE-15-SP1
        version = project.split(':')[1]
        parts = version.split('-')
        version = float(parts[1])
        if len(parts) > 2:
            # Service pack.
            version += float(parts[2][2:]) / 10
        #print(project, version)
        return version

    return None
