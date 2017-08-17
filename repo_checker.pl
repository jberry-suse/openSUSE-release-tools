#! /usr/bin/perl -w

use File::Basename;
use File::Temp qw/ tempdir  /;
use XML::Simple;
use Data::Dumper;
use Cwd;

use strict;

BEGIN {
  my ($wd) = $0 =~ m-(.*)/- ;
  $wd ||= '.';
  unshift @INC, $wd;
}

require CreatePackageDescr;

my $ret = 0;
my $arch = shift @ARGV;
my $dir = shift @ARGV;
my $dir_sub;
my %toignore;
my $repodir;
my %whitelist;
while (@ARGV) {
    my $switch = shift @ARGV;
    if ( $switch eq "-f" ) {
        my $toignore = shift @ARGV;
        open( TOIGNORE, $toignore ) || die "can't open $toignore";
        while (<TOIGNORE>) {
            chomp;
            $toignore{$_} = 1;
        }
        close(TOIGNORE);
    }
    elsif ( $switch eq "-r" ) {
        $repodir = shift @ARGV;
    }
    elsif ( $switch eq "-s" ) {
        $dir_sub = shift @ARGV;
    }
    elsif ( $switch eq "-w" ) {
        %whitelist = map { $_ => 1 } split(/\,/, shift @ARGV);
    }
    else {
        print "read the source luke: $switch ? \n";
        exit(1);
    }
}

my %targets;

sub write_package($$) {
    my $ignore  = shift;
    my $package = shift;

    my $name = basename($package);
    if ($name =~ m/^[a-z0-9]{32}-/) { # repo cache
       $name =~ s,^[^-]+-(.*)\.rpm,$1,;
    } else {
       $name =~ s,^(.*)-[^-]+-[^-]+.rpm,$1,;
    }

    if ( $ignore == 1 && defined $toignore{$name} ) {
        return;
    }

#     if ($ignore == 1 && $package =~ /containerd-test/) {
#         print STDERR "wut: $package";
#         exit(120);
#     }

    my $out = CreatePackageDescr::package_snippet($package);
    if ($out eq "" || $out =~ m/=Pkg:    /) {
        print STDERR "ERROR: empty package snippet for: $name\n";
        exit(126);
    }
    print PACKAGES $out;
    return $name;
}

my @rpms;
my $tmpdir = tempdir( "repochecker-XXXXXXX", TMPDIR => 1, CLEANUP => 1 );
my $pfile = $tmpdir . "/packages";
open( PACKAGES, ">", $pfile ) || die 'can not open';
print PACKAGES "=Ver: 2.0\n";

# Allow $repodir to be empty indicating only to review $dir.
if (length($repodir)) {
    my @rpms = glob("$repodir/*.rpm");
    foreach my $package (@rpms) {
        write_package(1, $package);
    }
}

@rpms = glob("$dir/*.rpm");
foreach my $package (@rpms) {
    my $name = write_package( 0, $package );
    if (!exists($whitelist{$name})) {
        $targets{$name} = 1;
    }
}

# if ($dir =~ m/.*?:Staging:[A-Z]$/) {
if (length($dir_sub)) {
#     $dir = $dir . ":DVD";
    @rpms = glob("$dir_sub/*.rpm");
    foreach my $package (@rpms) {
#         print("$package\n");
        my $name = write_package( 0, $package );
        if (!exists($whitelist{$name})) {
            $targets{$name} = 1;
        }
    }
#     exit(1);
}

close(PACKAGES);

my $error_file = $tmpdir . "/error_file";
open(INSTALL, "/usr/bin/installcheck $arch $pfile 2> $error_file |")
  || die 'exec installcheck';
my $inc = 0;
while (<INSTALL>) {
    chomp;

    next if (/^unknown line:.*Flx/);
    if ($_ =~ /^[^ ]/) {
        $inc = 0;
    }
    if ( $_ =~ /^can't install (.*)-[^-]+-[^-]+:$/ ) {
#         $inc = 0;

        if ( defined $targets{$1} ) {
            $inc = 1;
            $ret = 1;
        }
    }
    if ($inc) {
        print "$_\n";
    }
}
close(INSTALL);

open(ERROR, '<', $error_file);
while (<ERROR>) {
    chomp;
    print STDERR "$_\n";
    $ret = 1;
}
close(ERROR);

my $cmd = sprintf( "perl %s/findfileconflicts $pfile", dirname($0) );
open(CONFLICTS, "$cmd 2> $error_file |") || die 'exec fileconflicts';
$inc = 0;
while (<CONFLICTS>) {
    chomp;

    if ($_ =~ /^[^ ]/) {
        $inc = 0;
    }
    if ( $_ =~ /^found conflict of (.*)-[^-]+-[^-]+ with (.*)-[^-]+-[^-]+:$/ ) {
#         $inc = 0;

        if ( defined $targets{$1} || defined $targets{$2} ) {
            $inc = 1;
            $ret = 1;
        }
    }
    if ($inc) {
        print "$_\n";
    }
}
close(CONFLICTS);

open(ERROR, '<', $error_file);
while (<ERROR>) {
    chomp;
    print STDERR "$_\n";
    $ret = 1;
}
close(ERROR);

exit($ret);
