	subroutine allist(lend)
	implicit integer*4 (i-n)

#ccc  name: allist.r
#ccc  version date: 7-24-85
#ccc  author(s): Kathy Kierein
#ccc  language: ratfor
#ccc
#ccc  short description: This subroutine prints the list of alias
#ccc			 words and their translations either onto
#ccc			 the screen or into a file. Used in specpr.
#ccc
#ccc  algorithm description: none
#ccc  system requirements: none
#ccc  subroutines called: none
#ccc  argument list description:
#ccc  parameter description:
#ccc  common description:
#ccc  message files referenced:
#ccc  internal variables: fbeg-position of the first character in
#ccc				the filename
#ccc			  fend-position of the last character in
#ccc				the filename
#ccc			  fsize-the size of the filename
#ccc  file description:
#ccc  user command lines:
#ccc  update information:
#ccc  NOTES:
#ccc
# This subroutine prints the list of alias words and their
# translations either in a file or on the screen


	include "../common/spmaxes"   # max parameters, must be first

	include "../common/key1"
	include "../common/lbl4"
	include "../common/lundefs"
	include "../common/lblvol"

#RED
	integer*4 lnb       # function lnb

	character*80 filenm

	integer*4 fbeg, fend, fsize, ier, idummy, lend

	character*1 ihbcksl

	ihbcksl = char(92)  # this is the backslash character

	fbeg = 0


# This finds the first letter in the filename

	do k=lend,80 {
		if (iopcon (k:k) != ' ') {
			fbeg = k
		}else{
			next
		}
		break
	}

# This checks for a default to the screen 

	if (fbeg == 0) {
		do i=1,numals {

			icom2 = acomsiz(i)   # size of a comment
			if (icom2 < 1) icom2 = 1
			write (ttyout,10) cmdals(i)(1:alsize(i)), 
					cmdtrn(i)(1:trnsiz(i)),
					cmdcomm(i)(1:icom2)
10			format ('==[',a,']',a, 5x, a)
		}
		write (ttyout,12)  numals, SPMAXALIAS
12		format ('\\# used ',i5,'  out of ',i5,' aliases')
	}else{

# This prints the list to the specified file
 
		fend = lnb (iopcon(1:80))
		fsize = fend - fbeg + 1

		filenm (1:fsize) = iopcon (fbeg:fend)

# This closes restart file and opens the file to write to

		close (rlun, iostat=idummy)
		if (idummy != 0) {
			write (ttyout,5) idummy
5			format ('Error in closing the restart file.',
				' Subroutine allist.r. iostat=',i4)
			return
		}

		open (rlun,file = filenm,access = 'sequential',
			form = 'formatted', status = 'unknown',
			iostat = ier)
		if (ier != 0) {
			write(ttyout,30)  filenm,ier
30			format ('Error in opening file',a,
				' in subroutine allist.r.',
				' ier =',i4)
			return
		}
		do j=1,numals {
			icom2 = acomsiz(i)   # size of a comment
			if (icom2 < 1) icom2 = 1
			write (rlun,20) cmdals(j)(1:alsize(j)),
					cmdtrn(j)(1:trnsiz(j)),
					cmdcomm(j)(1:icom2)
20			format ('==[',a,']',a, 5x, a)
		}
		write (rlun,22)  numals, SPMAXALIAS
22		format ('\\# used ',i5,'  out of ',i5,' aliases')

		# This closes file and reopens restart file

		close (rlun, iostat=idummy)
		if (idummy != 0) {
			write (ttyout,40) filenm,idummy
40		format ('Error in closing file ',a,'in subroutine allist.r',
			'idummy =',i4)
		}

		# KEL: open rlun within rstart only  (option 4 opens rlun only)

		call rstart(4)

	}




	return
	end
