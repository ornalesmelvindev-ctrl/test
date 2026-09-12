I mean what I want is extra feature like devtool modify, so I just need to open vscode in local, then I can apply
in the build server, I mean because when we do devtool modify, it is in the yocto_root/build/workspace/sources right?
what I want to do now is upon opnening the repo locally, it do stash, pull, and pop right?
Like in the build server part of the app, users must put the yocto_root path(where poky lies)
 cd dev/project/ewd-orv3-pmc/artesyn-shelf
╭─░▒▓ ~/dev/project/ewd-orv3-pmc/artesyn-shelf  develop *1 ?2 ······································ ✔  melvin@ewdsabuild  07:52:47 AM ▓▒░
╰─ ls
build  ewd-meta-orv3-artesyn  meta-aspeed  meta-openembedded  poky

then ofcourse, we need to do source poky/oe-init-build-env to able to use the tools like bitbake and devtool

What I want is upon opening the repo in the vcscode locally, then
in the build server part, like theres a devtool modify button, then list will pop(upto you if scrollable or what), list of all repo,
I mean all list acan be all seen but the one that is being modified locally only will be clickable to devtool modify, what which
internally, what it do is 
given we have the yocto_root path,
source poky/oe-init-build-env, then devtool build <repo> (take note the name of the recipe is like psu-poll-manager) like just
remove 'ewd-orv3-' in the https links, then what you will do there is again, git checkout develop, git stash, git pull, then no git
stash pop, then locally, for example, we have changes right? what you will do is when I click that devtool modify option, is to
git diff local changes, then apply that diff in the dev/project/ewd-orv3-pmc/artesyn-shelf/build/workspace/source/psu-poll-manager
for example if psu-poll-manager is the one being modified, I mean you understand it right?,
    then after that, add option as well in the app, to build(in this case, it will do bitbake -c clean recipe && bitbake recipe)
    then ofcourse, there a deploy button, at which you will do devtool deploy-target on the device ip, can you do it? or do you
    have any better idea and addtional feature for it? I mean I want all the development to be in my app, I think you can also
    add the show build logs in the app, like to know whether why the build failed, or error or warning, you know, and make sure the highlight
    the bugs like error, warning, etc.