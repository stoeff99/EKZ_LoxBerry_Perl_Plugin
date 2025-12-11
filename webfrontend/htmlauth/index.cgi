#!/usr/bin/perl

# Copyright 2025 Christian Stoeffel
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

##########################################################################
# Modules
##########################################################################

use CGI;
use LoxBerry::System;
use LoxBerry::Web;
use warnings;
use strict;

##########################################################################
# Variables
##########################################################################

# Read Form
my $cgi = CGI->new;
my $q = $cgi->Vars;

my $version = LoxBerry::System::pluginversion();
my $template;

# Language Phrases
my %L;

##########################################################################
# AJAX
##########################################################################

if( $q->{ajax} ) {
	
	## Handle all ajax requests 
	require JSON;
	my %response;
	ajax_header();

	# Example: Get status
	if( $q->{ajax} eq "getstatus" ) {
		$response{message} = "EKZ Plugin Running";
		$response{error} = 0;
		print JSON->new->canonical(1)->encode(\%response);
	}
	
	exit;

##########################################################################
# Normal request (not AJAX)
##########################################################################

} else {
	
	# Init Template
	$template = HTML::Template->new(
	    filename => "$lbptemplatedir/index.html",
	    global_vars => 1,
	    loop_context_vars => 1,
	    die_on_bad_params => 0,
	);
	%L = LoxBerry::System::readlanguage($template, "language.ini");
	
	# Default is main form
	$q->{form} = "main" if !$q->{form};

	if ($q->{form} eq "main") { &form_main() }
	elsif ($q->{form} eq "settings") { &form_settings() }
	elsif ($q->{form} eq "log") { &form_log() }

	# Print the form
	&form_print();
}

exit;

##########################################################################
# Form: Main
##########################################################################

sub form_main
{
	$template->param("FORM_MAIN", 1);
	return();
}

##########################################################################
# Form: Settings
##########################################################################

sub form_settings
{
	$template->param("FORM_SETTINGS", 1);
	return();
}

##########################################################################
# Form: Log
##########################################################################

sub form_log
{
	$template->param("FORM_LOG", 1);
	$template->param("LOGLIST", LoxBerry::Web::loglist_html());
	return();
}

##########################################################################
# Print Form
##########################################################################

sub form_print
{
	
	# Navbar
	our %navbar;

	$navbar{10}{Name} = "Main";
	$navbar{10}{URL} = 'index.cgi?form=main';
	$navbar{10}{active} = 1 if $q->{form} eq "main";
	
	$navbar{20}{Name} = "Settings";
	$navbar{20}{URL} = 'settings.cgi';
	$navbar{20}{active} = 1 if $q->{form} eq "settings";
	
	$navbar{98}{Name} = "Log";
	$navbar{98}{URL} = 'index.cgi?form=log';
	$navbar{98}{active} = 1 if $q->{form} eq "log";
	
	# Template
	LoxBerry::Web::lbheader("EKZ Dynamic Price V$version", "https://wiki.loxberry.de", "");
	print $template->output();
	LoxBerry::Web::lbfooter();
	
	exit;

}

######################################################################
# AJAX functions
######################################################################

sub ajax_header
{
	print $cgi->header(
			-type => 'application/json',
			-charset => 'utf-8',
			-status => '200 OK',
	);	
}

END {
}

