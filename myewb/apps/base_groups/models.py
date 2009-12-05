"""myEWB base groups models declarations

This file is part of myEWB
Copyright 2009 Engineers Without Borders (Canada) Organisation and/or volunteer contributors
Some code derived from Pinax, copyright 2008-2009 James Tauber and Pinax Team, licensed under the MIT License

Last modified on 2009-07-29
@author Joshua Gorner, Benjamin Best
"""

import datetime
import re
import unicodedata

from django.core.urlresolvers import reverse
from django.contrib.auth.models import  User
from django.utils.translation import ugettext_lazy as _
from django.db import models, connection
from django.db.models.signals import post_save, pre_delete
from django.core.mail import EmailMessage

from emailconfirmation.models import EmailAddress

from siteutils.helpers import get_email_user
from groups.base import Group
from wiki.models import Article

class BaseGroup(Group):
    """Base group (from which networks, communities, projects, etc. derive).
    
    Not intended to be instantiated by itself.
    """
    
    model = models.CharField(_('group model'), max_length=500, null=True, blank=True)
    parent = models.ForeignKey('self', related_name="children", verbose_name=_('parent'), null=True, blank=True)
    
    member_users = models.ManyToManyField(User, through="GroupMember", verbose_name=_('members'))
    
    # private means members can only join if invited
    private = models.BooleanField(_('private'), default=False)
    
    VISIBILITY_CHOICES = (
        ('E', _("everyone")),
        ('P', _("group members and members of parent network only")),
        ('M', _("group members only"))
    )
    visibility = models.CharField(_('visibility'), max_length=1, choices=VISIBILITY_CHOICES, default='E')
    
    whiteboard = models.ForeignKey(Article, related_name="group", verbose_name=_('whiteboard'), null=True)
    
    def is_visible(self, user):
        visible = False
        if self.visibility == 'E':
            visible = True
        elif user.is_authenticated():
            if user.is_superuser:
                return True
            
            member_list = self.members.filter(user=user, request_status='A')
            if member_list.count() > 0:
                visible = True
            elif self.visibility == 'P':
                parent_member_list = self.parent.members.filter(user=user, request_status='A')
                if parent_member_list.count() > 0:
                    visible = True
        return visible
    
    def user_is_member(self, user):
        return user.is_authenticated() and (self.members.filter(user=user, request_status='A').count() > 0)
        
    def user_is_member_or_pending(self, user):
        return user.is_authenticated() and (self.members.filter(user=user).count() > 0)
            
    def user_is_admin(self, user):
        return user.is_authenticated() and \
            ((self.members.filter(user=user, request_status='A', is_admin=True).count() > 0) or user.is_superuser)

    def get_absolute_url(self):
        return reverse('group_detail', kwargs={'group_slug': self.slug})

    def get_member_emails(self):
        members_with_emails = self.get_accepted_members().select_related(depth=1).exclude(user__email='') | \
                self.members.filter(request_status='B').select_related(depth=1)
        return [member.user.email for member in members_with_emails]

    def add_member(self, user):
        """
        Adds a member to a group with the proper request_status.
        """
        request_status = user.has_usable_password() and 'A' or 'B'
        return GroupMember.objects.create(user=user, group=self, request_status=request_status)

    def send_mail_to_members(self, subject, body, html=True, fail_silently=False):
        """
        Creates and sends an email to all members of a network using Django's
        EmailMessage.
        Takes in a a subject and a message and an optional fail_silently flag.
        Automatically sets:
        from_email: group_name <group_slug@ewb.ca>
        to: list-group_slug@ewb.ca
        bcc: list of member emails
        """
        msg = EmailMessage(
                subject=subject, 
                body=body, 
                from_email='%s <%s@ewb.ca>' % (self.name, self.slug), 
                to=['list-%s@ewb.ca' % self.slug],
                bcc=self.get_member_emails(),
                )
        if html:
            msg.content_subtype = "html"
        msg.send(fail_silently=fail_silently)
    
    # TODO:
    # list of members (NOT CSV)

    def save(self, force_insert=False, force_update=False):
        # if we are updating a group, don't change the slug (for consistency)
        if not self.id:
            slug = self.slug
            slug = slug.replace(' ', '-')
            
            # translates accents into their regular-character equivalent
            # (http://snippets.dzone.com/posts/show/5499)
            slug = unicodedata.normalize('NFKD', unicode(slug)).encode('ASCII', 'ignore')
            
            #(?P<group_slug>[-\w]+) This is the
            # regex definition of a slug so if we don't match on this we 
            # should remove illegal characters.
            match = re.match(r'[-\w]+', slug)
            if match is None or not match.group(0) == slug:
                slug = re.sub(r'[^-\w]+', '', slug)
            
            # check if slug is in use; increment until we find a good one.
            # (is there anything better than numerical incrementing?)
            temp_groups = BaseGroup.objects.filter(slug__contains=slug, model=self.model)

            if (temp_groups.count() != 0):
                slugs = [n.slug for n in temp_groups]
                old_slug = slug
                i = 0
                while slug in slugs:
                    i = i + 1
                    slug = old_slug + "%d" % (i, )
            self.slug = slug
        super(BaseGroup, self).save(force_insert=force_insert, force_update=force_update)

    def get_url_kwargs(self):
        return {'group_slug': self.slug}
        
    def get_visible_children(self, user):
        if not user.is_authenticated():
            return self.children.filter(visibility='E')
        elif user.is_superuser:
            return self.children.all()
        else:
            children = self.children.filter(visibility='E') | self.children.filter(member_users=user)
            if self.user_is_member(user):
                children = children | self.children.filter(visibility='P')
            return children.distinct()
            
    def get_accepted_members(self):
        return self.members.filter(request_status='A')

class BaseGroupMember(models.Model):
    is_admin = models.BooleanField(_('admin'), default=False)
    admin_title = models.CharField(_('admin title'), max_length=500, null=True, blank=True)
    admin_order = models.IntegerField(_('admin order (smallest numbers come first)'), default=999)
    joined = models.DateTimeField(_('joined'), default=datetime.datetime.now)
    
    REQUEST_STATUS_CHOICES = (
        ('A', _("accepted")),
        ('I', _("invited")),
        ('R', _("requested")),
        ('B', _("bulk member")),
    )
    request_status = models.CharField(_('request status'), max_length=1, choices=REQUEST_STATUS_CHOICES, default='A')

    class Meta:
        abstract = True
        ordering = ('is_admin', 'admin_order')
    
    @property
    def is_accepted(self):
        return self.request_status == 'A'
    
    @property
    def is_invited(self):
        return self.request_status == 'I'
        
    @property
    def is_requested(self):
        return self.request_status == 'R'

    @property
    def is_bulk(self):
        return self.request_status == 'B'

    def __unicode__(self):
        return "%s - %s (%s)" % (self.user, self.group, self.request_status)

class GroupMember(BaseGroupMember):
    """
    Non-abstract representation of BaseGroupMember. Base class is required
    for GroupMemberRecord.
    """
    # had to double these two fields in this model and GroupMemberRecord due to issues with related_name. 
    # See http://docs.djangoproject.com/en/dev/topics/db/models/#be-careful-with-related-name
    group = models.ForeignKey(BaseGroup, related_name="members", verbose_name=_('group'))
    user = models.ForeignKey(User, related_name="member_groups", verbose_name=_('user'))
    # away = models.BooleanField(_('away'), default=False)
    # away_message = models.CharField(_('away_message'), max_length=500)
    # away_since = models.DateTimeField(_('away since'), default=datetime.now)

class GroupMemberRecord(BaseGroupMember):
    """
    A snapshot of a user's group status at a particular point in time.
    """
    # had to double these two fields in this model and GroupMember due to issues with related_name. 
    # See http://docs.djangoproject.com/en/dev/topics/db/models/#be-careful-with-related-name
    group = models.ForeignKey(BaseGroup, related_name="member_records", verbose_name=_('group'))
    user = models.ForeignKey(User, related_name="group_records", verbose_name=_('user'))
    datetime = models.DateTimeField(auto_now_add=True)

    def __init__(self, *args, **kwargs):
        instance = kwargs.pop('instance', None)
        super(GroupMemberRecord, self).__init__(*args, **kwargs)
        if instance is not None:
            # copy over all properties from the instance provided
            # note that these override any values passed to the constructor
            self.group = instance.group
            self.user = instance.user
            self.is_admin = instance.is_admin
            self.admin_title = instance.admin_title
            self.admin_order = instance.admin_order
            self.joined = instance.joined
            self.request_status = instance.request_status


def group_member_snapshot(sender, instance, **kwargs):
    """
    Takes a snapshot of a GroupMember object each time is
    saved.
    """
    record = GroupMemberRecord(instance=instance)
    record.save()
post_save.connect(group_member_snapshot, sender=GroupMember, dispatch_uid='groupmembersnapshot')

def end_group_member_snapshot(sender, instance, **kwargs):
    """
    Takes the final snapshot of a group member as it is deleted.
    Sets the request_status to 'E' for ended.
    """
    record = GroupMemberRecord(instance=instance)
    record.request_status = 'E' # for ended
    record.save()
pre_delete.connect(end_group_member_snapshot, sender=GroupMember, dispatch_uid='endgroupmembersnapshot')
            
    
class GroupLocation(models.Model):
    group = models.ForeignKey(BaseGroup, related_name="locations", verbose_name=_('group'))
    place = models.CharField(max_length=100, null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

def clean_up_bulk_users(sender, instance, created, **kwargs):
    if instance.verified:
        # XXX Warning! This only works because get_email_user returns
        # the user with their email set to the argument before it returns
        # users with EmailAddress's with the argument
        email_user = get_email_user(instance.email)
        user = instance.user
        # a 
        if not email_user == user:
            for membership in email_user.member_groups.all():
                if membership.request_status == 'B' and not user.member_groups.filter(group=membership.group):
                    membership.user = instance.user
                    membership.request_status = 'A'
                    membership.save()
            # delete old bulk user - should delete GroupMember objects as well
            email_user.delete()

post_save.connect(clean_up_bulk_users, sender=EmailAddress)

def add_creator_to_group(sender, instance, created, **kwargs):
    if created:
        try:
            GroupMember.objects.create(
                    user=instance.creator, 
                    group=instance,
                    is_admin=True,
                    admin_title='%s Creator' % instance.name,
                    admin_order = 1)
        except:
            pass
post_save.connect(add_creator_to_group, sender=BaseGroup)
